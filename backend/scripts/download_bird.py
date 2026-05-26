"""Skripta za preuzimanje BIRD Mini-Dev dataset-a.

BIRD Mini-Dev (500 pitanja s SQLite, MySQL, i PostgreSQL gold SQL-om) je
standardni subset BIRD benchmark-a koji se koristi za brže iteracije.
Koristi se isključivo SQLite varijanta jer naša filozofija (vidi plan,
Faza 4) je da gold SQL ostaje u originalnom dialect-u za metodološku
čistoću rezultata.

Pokretanje (unutar backend kontejnera, jednokratno):

    docker compose exec backend python scripts/download_bird.py

Output struktura nakon uspjeha:

    /app/data/bird_mini/
    ├── questions.json                              (učitamo iz mini_dev_sqlite.json)
    └── databases/
        ├── california_schools/
        │   └── california_schools.sqlite
        ├── financial/
        │   └── financial.sqlite
        └── ...

Skripta je idempotentna — ako su pitanja i baze već prisutni, samo
provjeri integritet i exit.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

# Aliyun OSS mirror — direktan zip s kompletnim Mini-Dev dataset-om.
# (HuggingFace dataset zahtjeva poseban CLI za download; OSS zip je samostalan.)
BIRD_MINI_DEV_URL = "https://bird-bench.oss-cn-beijing.aliyuncs.com/minidev.zip"

# Lokalni izlazni folder unutar kontejnera.
DATASET_DIR = Path("/app/data/bird_mini")
QUESTIONS_FILE = DATASET_DIR / "questions.json"
DATABASES_DIR = DATASET_DIR / "databases"


def main() -> int:
    if QUESTIONS_FILE.exists() and DATABASES_DIR.exists():
        question_count = _count_questions(QUESTIONS_FILE)
        db_count = sum(1 for _ in DATABASES_DIR.iterdir() if _.is_dir())
        print(f"BIRD Mini-Dev već postoji ({question_count} pitanja, {db_count} baza).")
        print("Skip download. Ako želiš ponoviti, obriši /app/data/bird_mini.")
        return 0

    print(f"Preuzimam BIRD Mini-Dev s {BIRD_MINI_DEV_URL} …")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        zip_path = tmp_path / "minidev.zip"

        # Download s progress feedbackom — zip može biti 1-2 GB pa nije zanemarivo.
        _download_with_progress(BIRD_MINI_DEV_URL, zip_path)

        print(f"\nRaspakiravam u {tmp_path} …")
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp_path)

        # Mini-Dev zip ima nestabilnu top-level strukturu (može biti
        # "minidev/MINIDEV/..." ili samo "MINIDEV/..."). Auto-detect:
        # tražimo folder koji sadrži `dev_databases/` i json file-ove.
        source_root = _find_dataset_root(tmp_path)
        if source_root is None:
            print("ERROR: ne mogu pronaći BIRD Mini-Dev folder u raspakiranom zip-u.")
            _debug_list(tmp_path)
            return 1

        print(f"\nPronađen dataset root: {source_root}")
        _organize_into_target(source_root, DATASET_DIR)

    # Verifikacija
    question_count = _count_questions(QUESTIONS_FILE)
    db_count = sum(1 for _ in DATABASES_DIR.iterdir() if _.is_dir())
    print(f"\nGotovo. {question_count} pitanja, {db_count} baza dostupno u {DATASET_DIR}.")
    return 0


def _download_with_progress(url: str, dest: Path) -> None:
    """Stream download s povremenim progress logom."""

    def hook(block_num: int, block_size: int, total_size: int) -> None:
        # Ispisuje napredak na svakih ~5 MB; izbjegava preplavljivanje terminala.
        downloaded = block_num * block_size
        if total_size > 0 and block_num % 256 == 0:
            mb = downloaded / 1_048_576
            total_mb = total_size / 1_048_576
            pct = 100 * downloaded / total_size
            print(f"  {mb:.1f} / {total_mb:.1f} MB ({pct:.1f}%)", end="\r", flush=True)

    urllib.request.urlretrieve(url, dest, reporthook=hook)


def _find_dataset_root(extraction_path: Path) -> Path | None:
    """Pronađi folder koji sadrži BIRD strukturu, gdje god se nalazi u zip-u."""

    # Walk-aj sve folder-e, vrati onaj koji ima `dev_databases` poddirektorij
    # ili koji direktno sadrži .sqlite file-ove (mini_dev_data layout).
    candidates: list[Path] = []
    for path in extraction_path.rglob("*"):
        if not path.is_dir():
            continue
        if (path / "dev_databases").is_dir():
            candidates.append(path)
        elif any(child.suffix == ".sqlite" for child in path.iterdir() if child.is_file()):
            # Već smo u `databases/<db_id>/` levelu — root je 2 razine više
            candidates.append(path.parent.parent)

    if not candidates:
        return None
    # Uzmi najpliće (ako je više kandidata).
    return min(candidates, key=lambda p: len(p.parts))


def _organize_into_target(source_root: Path, target: Path) -> None:
    """Skupi pitanja i baze iz source-a u našu očekivanu strukturu."""

    target.mkdir(parents=True, exist_ok=True)
    DATABASES_DIR.mkdir(exist_ok=True)

    # Pitanja — biramo SQLite verziju (mini_dev_sqlite.json) jer BIRD literatura
    # tu radi evaluation. Bird redistribuira i mysql/postgres verzije ali to su
    # transpiled iz SQLite-a, nepouzdane za našu metodu.
    sqlite_json = _first_match(source_root, "mini_dev_sqlite.json")
    if sqlite_json is None:
        raise FileNotFoundError("mini_dev_sqlite.json nije pronađen u extracted folderu.")
    print(f"  pitanja: {sqlite_json} → {QUESTIONS_FILE}")
    shutil.copy2(sqlite_json, QUESTIONS_FILE)

    # Baze — kopiraj cijeli `dev_databases` folder; preimenuj u `databases`.
    src_databases = source_root / "dev_databases"
    if not src_databases.is_dir():
        # Fallback — naivno: prvi folder pod `databases/` imenom
        src_databases = _first_match_dir(source_root, "dev_databases") or _first_match_dir(source_root, "databases")
    if src_databases is None or not src_databases.is_dir():
        raise FileNotFoundError("dev_databases folder nije pronađen.")

    print(f"  baze:    {src_databases} → {DATABASES_DIR}")
    for db_folder in src_databases.iterdir():
        if not db_folder.is_dir():
            continue
        dst = DATABASES_DIR / db_folder.name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(db_folder, dst)


def _first_match(root: Path, name: str) -> Path | None:
    for path in root.rglob(name):
        if path.is_file():
            return path
    return None


def _first_match_dir(root: Path, name: str) -> Path | None:
    for path in root.rglob(name):
        if path.is_dir():
            return path
    return None


def _count_questions(json_path: Path) -> int:
    try:
        with json_path.open(encoding="utf-8") as f:
            data = json.load(f)
        return len(data) if isinstance(data, list) else 0
    except Exception:
        return 0


def _debug_list(path: Path, max_depth: int = 3) -> None:
    """Ispisuje stablo direktorija — diagnostika kad auto-detect ne uspije."""

    print("\n--- Sadržaj raspakiranog foldera ---")
    for item in sorted(path.rglob("*"))[:50]:
        depth = len(item.relative_to(path).parts)
        if depth > max_depth:
            continue
        prefix = "  " * (depth - 1)
        marker = "/" if item.is_dir() else ""
        print(f"{prefix}{item.name}{marker}")


if __name__ == "__main__":
    sys.exit(main())
