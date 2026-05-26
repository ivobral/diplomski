"""CLI za pokretanje BIRD benchmark-a iz terminala (bez UI-a).

Primjeri:

    # Pilot run (10 pitanja, 2 providera, 4 strategije, security suita)
    docker compose exec backend python /app/scripts/run_benchmark_cli.py \\
        --providers gemini openai \\
        --strategies A B C D \\
        --limit 10

    # Main run (50 pitanja, samo D strategija, jedan provider)
    docker compose exec backend python /app/scripts/run_benchmark_cli.py \\
        --providers gemini \\
        --strategies D \\
        --limit 50

    # Final run (200 pitanja, sve), bez security suite-a (već dokazano)
    docker compose exec backend python /app/scripts/run_benchmark_cli.py \\
        --providers gemini openai \\
        --strategies A B C D \\
        --limit 200 \\
        --no-security

Output:
    data/benchmark_runs/<run_id>.json    — full report
    data/benchmark_runs/<run_id>.csv     — aggregated metrics tablica

Predviđeno vrijeme:
    Pilot (80 poziva, Gemini)  ~3-5 min
    Main  (400 poziva, Gemini) ~15-20 min
    Final (800-1600 poziva)    ~30-90 min
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
from pathlib import Path

from app.api.deps import get_schema_inspector
from app.config import settings
from app.core.logging import configure_logging, get_logger
from app.evaluation.bird_loader import BirdLoader
from app.evaluation.few_shot_retrieval import FewShotRetriever
from app.evaluation.runner import BenchmarkRunner, run_to_dict
from app.llm.factory import create_llm_provider_for
from app.llm.prompts.builder import PromptBuilder
from app.services.benchmark_executor import BenchmarkExecutor
from app.services.benchmark_query_service import BenchmarkQueryService
from app.services.self_consistency import DEFAULT_N_SAMPLES
from app.validation.validator import SqlValidator

logger = get_logger(__name__)

OUTPUT_DIR = Path("/app/data/benchmark_runs")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BIRD Mini-Dev benchmark CLI")
    parser.add_argument(
        "--providers",
        nargs="+",
        required=True,
        help="Imena providera (npr. gemini openai ollama). Moraju biti konfigurirani u .env.",
    )
    parser.add_argument(
        "--strategies",
        nargs="+",
        choices=["A", "B", "C", "D"],
        default=["A", "B", "C", "D"],
        help="Lista strategija za testiranje (default: sve 4).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Broj BIRD pitanja (default 10 za pilot).",
    )
    parser.add_argument(
        "--difficulty",
        choices=["simple", "moderate", "challenging"],
        default=None,
        help="Filter po BIRD difficulty (default: sve težine).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=3,
        help="Maksimalan broj paralelnih LLM zahtjeva (default 3, konzervativno za rate limit).",
    )
    parser.add_argument(
        "--no-security",
        action="store_true",
        help="Preskoči security suite (default: pokreće je).",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help=(
            "Per-run override imena modela (vrijedi za sve --providers u ovom run-u). "
            "Bez restarta kontejnera. Primjer: --providers openai --model gpt-4o-mini. "
            "Default: koristi se model iz .env varijable provider-a (OPENAI_MODEL, GEMINI_MODEL, itd.)."
        ),
    )
    # ----- Pipeline ekstenzije (D-only, opt-in) -----
    # Sve ovi flag-ovi utječu samo na strategiju D. Razlog: A/B/C su baseline-i
    # za usporedbu — moramo ih ostaviti netaknute da ablation ima smisla.
    parser.add_argument(
        "--self-consistency",
        action="store_true",
        help="Uključi self-consistency: N=5 LLM poziva, glasanje po rezultatu (5x trošak, +10-15pp EX).",
    )
    parser.add_argument(
        "--n-samples",
        type=int,
        default=DEFAULT_N_SAMPLES,
        help=f"Broj samples-a za self-consistency (default {DEFAULT_N_SAMPLES}; aktivno samo s --self-consistency).",
    )
    parser.add_argument(
        "--few-shot",
        action="store_true",
        help="Uključi retrieval-augmented few-shot: TF-IDF k=3 sličnih BIRD pitanja kao primjeri u prompt-u (DAIL-SQL).",
    )
    parser.add_argument(
        "--column-linking",
        action="store_true",
        help="Uključi column-level schema linking nakon table-level (manje schema noise, više preciznosti).",
    )
    parser.add_argument(
        "--entity-extraction",
        action="store_true",
        help="Uključi entity extraction + DB value mapping (case-mismatch fix za WHERE literale).",
    )
    parser.add_argument(
        "--value-check",
        action="store_true",
        help="Uključi value-aware validation: warning + retry ako WHERE literali ne postoje u bazi case-sensitive.",
    )
    parser.add_argument(
        "--all-improvements",
        action="store_true",
        help="Shortcut: uključi sva 5 poboljšanja odjednom (self-consistency, few-shot, column-linking, entity-extraction, value-check).",
    )
    parser.add_argument(
        "--cascade",
        action="store_true",
        help=(
            "Cascade strategija — pametna uvjetna aktivacija improvements po failure mode-u. "
            "Layer 1: D-basic; Layer 2 (na validation fail): + column-linking; "
            "Layer 3 (na suspicious result): + self-consistency. "
            "Izolira interference koji se javlja s --all-improvements. "
            "Aktivno samo za strategiju D."
        ),
    )
    parser.add_argument(
        "--llm-judge",
        action="store_true",
        help=(
            "Cascade v3: doda LLM-as-Judge nakon Layer 1 koji semantički procjenjuje "
            "je li rezultat plausible odgovor na pitanje. Ako kaže NE, trigerira Layer 3 "
            "(self-consistency). Hvata greške koje verify_result heuristika propušta. "
            "+1 LLM call per question ako Layer 3 heuristika nije već triggerirala. "
            "Aktivno samo s --cascade."
        ),
    )
    return parser.parse_args()


async def main(args: argparse.Namespace) -> int:
    configure_logging()

    # 1. BIRD loader provjeri spremnost
    bird_loader = BirdLoader()
    if not bird_loader.is_ready():
        print("ERROR: BIRD Mini-Dev dataset nije preuzet.")
        print("Pokreni: docker compose exec backend python /app/scripts/download_bird.py")
        return 1
    print(f"BIRD dataset: {len(bird_loader.list_databases())} baza dostupno.")

    # 2. Provideri — instanciraj odabrane (s opcionalnim --model override-om)
    # NE koristi cached `get_llm_provider_for` jer override modela mora moći
    # raditi per-run; cache bi vratio staru instancu s old model-om.
    providers = {}
    for name in args.providers:
        try:
            providers[name] = create_llm_provider_for(name, model=args.model)
        except Exception as exc:
            print(f"ERROR: provider '{name}' nije konfiguriran: {exc}")
            return 1
    if args.model:
        print(f"Provideri: {list(providers.keys())} (model override: {args.model})")
    else:
        print(f"Provideri: {list(providers.keys())} (modeli iz .env)")

    # 3. Komponente (preko deps-a koji već postoji, ali ovdje ručno
    #    jer trebamo `sqlite` dialect varijante)
    inspector = get_schema_inspector()  # nije relevantan za BIRD ali validator treba referencu
    validator = SqlValidator(
        schema_inspector=inspector,
        default_limit=settings.DEFAULT_LIMIT,
        default_dialect="postgres",
    )
    # PromptBuilder s SQLite default-om za benchmark (override per call svejedno radi)
    prompt_builder = PromptBuilder(schema_inspector=inspector, default_dialect="sqlite")
    bench_executor = BenchmarkExecutor(
        dataset_path=Path("/app/data/bird_mini"),
        timeout_seconds=settings.QUERY_TIMEOUT_SECONDS,
    )

    # ----- Resolve --all-improvements shortcut -----
    if args.all_improvements:
        args.self_consistency = True
        args.few_shot = True
        args.column_linking = True
        args.entity_extraction = True
        args.value_check = True

    # ----- Few-shot retriever (build pool from full BIRD set) -----
    # Pool = svih BIRD pitanja (ne samo --limit-iranih). Per-question
    # `exclude_question_ids` u retrieveru osigurava leave-one-out.
    few_shot_retriever = None
    if args.few_shot:
        full_pool = bird_loader.load_questions(limit=None, difficulty=None)
        few_shot_retriever = FewShotRetriever(pool=full_pool)
        print(f"Few-shot pool: {len(full_pool)} pitanja indeksirano (TF-IDF).")

    bench_service = BenchmarkQueryService(
        prompt_builder=prompt_builder,
        validator=validator,
        executor=bench_executor,
        max_retry_attempts=settings.MAX_RETRY_ATTEMPTS,
        few_shot_retriever=few_shot_retriever,
        use_self_consistency=args.self_consistency,
        n_consistency_samples=args.n_samples,
        use_column_linking=args.column_linking,
        use_entity_extraction=args.entity_extraction,
        use_value_check=args.value_check,
        use_cascade=args.cascade,
        use_llm_judge=args.llm_judge,
    )

    # Pretty-print koja poboljšanja su aktivna (za log / repro)
    if args.cascade:
        # Cascade verzije:
        #  v1 = basic + CL + SC (uvjetni okidači)
        #  v2 = + few-shot u svim slojevima (always-on cheap boost)
        #  v3 = + LLM-as-Judge nakon Layer 1 kao dodatni Layer 3 okidač
        if args.few_shot and args.llm_judge:
            variant = "v3 (FS + CL + SC + Judge)"
        elif args.few_shot:
            variant = "v2 (FS + CL + SC)"
        elif args.llm_judge:
            variant = "v2.5 (CL + SC + Judge, no FS)"
        else:
            variant = "v1 (CL + SC)"
        print(f"Aktivna D-only poboljšanja: cascade {variant}")
        print("  Layer 1: basic" + (" + few-shot" if args.few_shot else ""))
        print("  Layer 2: + column-linking (na validation fail)")
        triggers = ["verify_heuristic"]
        if args.llm_judge:
            triggers.append("llm_judge")
        print(f"  Layer 3: + self-consistency (okidači: {', '.join(triggers)})")
        ignored = [
            name for flag, name in [
                (args.self_consistency, "self-consistency"),
                (args.column_linking, "column-linking"),
                (args.entity_extraction, "entity-extraction"),
                (args.value_check, "value-check"),
            ] if flag
        ]
        if ignored:
            print(f"  ⚠️  Cascade ignorira eksplicitne flagove: {', '.join(ignored)} (cascade ih sam aktivira po potrebi).")
    else:
        active = [
            name for flag, name in [
                (args.self_consistency, f"self-consistency(N={args.n_samples})"),
                (args.few_shot, "few-shot(k=3)"),
                (args.column_linking, "column-linking"),
                (args.entity_extraction, "entity-extraction"),
                (args.value_check, "value-check"),
            ] if flag
        ]
        if active:
            print(f"Aktivna D-only poboljšanja: {', '.join(active)}")
        else:
            print("Aktivna D-only poboljšanja: (none — basic D pipeline)")

    # 4. Runner
    runner = BenchmarkRunner(
        bird_loader=bird_loader,
        benchmark_service=bench_service,
        validator=validator,
        prompt_builder=prompt_builder,
        concurrency=args.concurrency,
    )

    # 5. Run! (uhvati iznimku da partial rezultati ipak budu spremljeni)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    run = None
    error_during_run: str | None = None
    try:
        run = await runner.run(
            providers=providers,
            strategy_codes=args.strategies,
            limit=args.limit,
            difficulty=args.difficulty,
            include_security=not args.no_security,
        )
    except Exception as exc:
        error_during_run = f"{type(exc).__name__}: {exc}"
        logger.exception("benchmark.run.exception")
        print(f"\n⚠️  Run je crashao: {error_during_run}")
        print("   Spremam što imam (možda partial)…")
    finally:
        # KRITIČNO: explicitly dispose-aj SQLAlchemy engine-e prije exit-a.
        # Bez ovog, aiosqlite background threadovi drže asyncio event loop
        # živim satima nakon "logically done"-a. Vidi `BenchmarkExecutor.dispose()`.
        print("Cleanup: disposing BenchmarkExecutor engines...", flush=True)
        try:
            await bench_executor.dispose()
            print("Cleanup OK.", flush=True)
        except Exception as exc:
            print(f"Cleanup warning: {exc}", flush=True)

    if run is None:
        # Stvarno nema ničeg za spremiti — fail clean
        print("Nemam BenchmarkRun objekt za spremiti.")
        return 1

    # 6. Export
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUTPUT_DIR / f"{run.run_id}.json"
    csv_path = OUTPUT_DIR / f"{run.run_id}.csv"

    payload = run_to_dict(run)
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"\nJSON: {json_path}")

    # CSV — agregati za quick view u Excel/Sheets
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "provider", "strategy", "total",
            "exact_match", "exact_match_rate",
            "execution_accuracy", "execution_accuracy_rate",
            "validation_success_rate", "error_rate",
            "blocked", "retry_used",
            "mean_llm_ms", "mean_total_ms",
            "total_input_tokens", "total_output_tokens",
        ])
        for agg in payload["aggregates"]:
            writer.writerow([
                agg["provider"], agg["strategy"], agg["total"],
                agg["exact_match"], agg["exact_match_rate"],
                agg["execution_accuracy"], agg["execution_accuracy_rate"],
                agg["validation_success_rate"], agg["error_rate"],
                agg["blocked"], agg["retry_used"],
                agg["mean_llm_ms"], agg["mean_total_ms"],
                agg["total_input_tokens"], agg["total_output_tokens"],
            ])
    print(f"CSV:  {csv_path}")

    # 7. Pretty-print najvažnijih brojki na ekranu
    print("\n" + "=" * 60)
    print(f"BENCHMARK SUMMARY (run_id={run.run_id})")
    print("=" * 60)
    for agg in payload["aggregates"]:
        print(f"  {agg['provider']:<30} × {agg['strategy']}")
        print(f"    Execution Accuracy: {agg['execution_accuracy_rate']*100:5.1f}%  ({agg['execution_accuracy']}/{agg['total']})")
        print(f"    Exact Match:        {agg['exact_match_rate']*100:5.1f}%  ({agg['exact_match']}/{agg['total']})")
        print(f"    Validation Success: {agg['validation_success_rate']*100:5.1f}%")
        print(f"    Mean total: {agg['mean_total_ms']:.0f} ms")
        print()

    if payload["security"]:
        sec = payload["security"]
        print("SECURITY")
        print(f"  direct_sql_rejection_rate:    {sec['direct_sql_rejection_rate']*100:5.1f}%")
        print(f"  nl_pipeline_rejection_rate:   {sec['nl_pipeline_rejection_rate']*100:5.1f}%")
        print(f"  overall_security_score:       {sec['overall_security_score']*100:5.1f}%")

    return 0


if __name__ == "__main__":
    args = parse_args()
    # Nuclear-option exit: ``os._exit`` umjesto ``sys.exit`` da bypass-amo
    # asyncio teardown koji zna visiti zbog zombie SQLite konekcija iz
    # cancellation-a u gold execution fazi. Naša best-effort dispose()
    # u finally bloku unutar main() je gotov u <3s po engine-u. Sve nakon
    # toga (Python interpreter exit handlers, asyncio loop close) je samo
    # kozmetika — JSON je već spremljen, podaci su sigurni.
    #
    # Ne koristimo ovaj pattern u library kodu, samo u CLI entrypoint-u.
    rc = asyncio.run(main(args))
    os._exit(rc)
