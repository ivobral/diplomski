"""Orkestrator za benchmark — povezuje BIRD pitanja s SQLite tijekom.

Zašto zaseban service umjesto da nadograđujemo glavni ``QueryService``:
- glavni service je dizajniran za Chinook/PostgreSQL demo,
- benchmark zahtijeva drugi executor (SQLite), drugi dialect, drugi schema
  source (per BIRD db_id), drugi rezultat-shape (sadrži gold SQL za usporedbu),
- razdvajanje održava oba tijeka čitka.

Workflow per pitanje:
    1. Dohvati shemu BIRD baze (per db_id, force_refresh za svaku baze prvog
       puta — engine se cache-ira u BenchmarkExecutoru).
    2. Sastavi prompt s odgovarajućom strategijom + sqlite dialect-om.
    3. Pozovi LLM.
    4. Validiraj s dialect="sqlite" i schema_override.
    5. Retry petlja (samo za strategy D; safety-blocked nikad ne retry-amo).
    6. Izvrši SQL kroz BenchmarkExecutor (read-only SQLite).
    7. Vrati BenchmarkQuestionResult sa svim detaljima za metrike.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.core.timing import Timer
from app.db.schema_inspector import DatabaseSchema, SchemaInspector
from app.evaluation.bird_descriptions import load_column_descriptions
from app.evaluation.bird_loader import BirdQuestion
from app.evaluation.few_shot_retrieval import (
    FewShotExample,
    FewShotRetriever,
    format_few_shot_examples,
)
from app.llm.base import BaseLLMProvider
from app.llm.prompts.builder import PromptBuilder
from app.llm.prompts.decomposition import build_decomposition_prompt, parse_decomposition
from app.llm.prompts.entity_extraction import build_entity_extraction_prompt, parse_entities
from app.llm.prompts.schema_linking import (
    build_column_linking_prompt,
    build_schema_linking_prompt,
    filter_schema_to_columns,
    filter_schema_to_tables,
    parse_linked_columns,
    parse_linked_tables,
)
from app.llm.prompts.strategies import get_strategy
from app.services.benchmark_executor import BenchmarkExecutor
from app.services.result_verifier import verify_result
from app.services.self_consistency import (
    DEFAULT_N_SAMPLES,
    execute_and_vote,
    generate_candidates,
)
from app.services.value_mapper import format_value_hints, map_entities_to_values
from app.validation.validator import SqlValidator

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BenchmarkQuestionResult:
    """Rezultat jednog pitanja u benchmark-u — sve što treba metrikama."""

    question_id: int
    db_id: str
    question: str
    difficulty: str
    strategy: str
    provider: str
    gold_sql: str
    predicted_sql: str | None
    normalized_sql: str | None

    validated: bool
    executed: bool
    blocked: bool
    error_reason: str | None
    retry_count: int

    # Mjerenja
    llm_ms: float
    validation_ms: float
    execution_ms: float
    total_ms: float
    input_tokens: int | None
    output_tokens: int | None

    # Rezultat izvršavanja (potrebno za execution accuracy usporedbu s gold)
    predicted_columns: list[str]
    predicted_rows: list[list[Any]]


class BenchmarkQueryService:
    """Orchestrator za benchmark tijek (SQLite, BIRD baze)."""

    def __init__(
        self,
        prompt_builder: PromptBuilder,
        validator: SqlValidator,
        executor: BenchmarkExecutor,
        max_retry_attempts: int,
        few_shot_retriever: FewShotRetriever | None = None,
        use_self_consistency: bool = False,
        n_consistency_samples: int = DEFAULT_N_SAMPLES,
        use_column_linking: bool = False,
        use_entity_extraction: bool = False,
        use_value_check: bool = False,
        use_cascade: bool = False,
        use_llm_judge: bool = False,
    ) -> None:
        self._prompt_builder = prompt_builder
        self._validator = validator
        self._executor = executor
        self._max_retry_attempts = max_retry_attempts
        # Pipeline ekstenzije — sve opt-in flagovi tako da basic D pipeline
        # iz Faze 4 ostane reproducibilan, a "+ retrieval + self-consistency"
        # mogu se uključiti/isključiti za eksperimentalne uspjehe ablation-a.
        self._few_shot_retriever = few_shot_retriever
        self._use_self_consistency = use_self_consistency
        self._n_consistency_samples = n_consistency_samples
        self._use_column_linking = use_column_linking
        self._use_entity_extraction = use_entity_extraction
        self._use_value_check = use_value_check
        # Cascade mode = pametna kombinacija. Ako True, ostali `use_*` flagovi
        # se IGNORIRAJU jer cascade sam kontrolira kad što uključuje. Single
        # flag tako razdvaja dvije filozofije: "uvijek-on improvement(s)" vs
        # "uvjetna aktivacija po failure mode-u".
        self._use_cascade = use_cascade
        # LLM-as-Judge: aktivira dodatni semantic check nakon Layer 1 koji
        # može trigerirati Layer 3 (SC) na rezultate koje verify_result
        # heuristika ne hvata. Cascade v3 = Cascade v2 + ovaj flag.
        self._use_llm_judge = use_llm_judge
        # Cache shema po db_id — schema introspection nije besplatna pa
        # ne radimo je za svako pitanje (BIRD ima 10-200 pitanja po bazi).
        self._schema_cache: dict[str, DatabaseSchema] = {}
        # Cache BIRD column descriptions po db_id (CSV parsing).
        self._descriptions_cache: dict[str, dict[tuple[str, str], str]] = {}

        # State holders za pre-step LLM metrike (zadnji poziv) — postavljaju
        # ih `_link_schema` i `_decompose_question`. Razdvojeno od povratne
        # vrijednosti jer su asinkroni metodi koji vraćaju domain-specific
        # rezultate (lista tablica / steps tekst), a metrike trebaju biti
        # akumulirane u `evaluate`.
        self._last_linker_llm_ms: float = 0.0
        self._last_linker_input_tokens: int | None = None
        self._last_linker_output_tokens: int | None = None
        self._last_decomp_llm_ms: float = 0.0
        self._last_decomp_input_tokens: int | None = None
        self._last_decomp_output_tokens: int | None = None
        # Entity extraction pre-step metrike
        self._last_entity_llm_ms: float = 0.0
        self._last_entity_input_tokens: int | None = None
        self._last_entity_output_tokens: int | None = None

    async def evaluate(
        self,
        question: BirdQuestion,
        strategy_code: str,
        provider: BaseLLMProvider,
    ) -> BenchmarkQuestionResult:
        """Javni entry point — dispatcher prema cascade ili single-pass tijek-u.

        Cascade je opt-in kroz konstruktor (``use_cascade=True``). Aktivan
        samo za strategiju D — A/B/C su baseline strategije i ne smiju biti
        izmijenjene cascade-om (inače bismo razbili ablation usporedbu).
        """

        if self._use_cascade and strategy_code == "D":
            return await self._evaluate_cascade(question, strategy_code, provider)
        return await self._one_pass(question, strategy_code, provider)

    async def _one_pass(
        self,
        question: BirdQuestion,
        strategy_code: str,
        provider: BaseLLMProvider,
        *,
        force_self_consistency: bool | None = None,
        force_column_linking: bool | None = None,
        force_entity_extraction: bool | None = None,
        force_value_check: bool | None = None,
        force_few_shot: bool | None = None,
    ) -> BenchmarkQuestionResult:
        """Jedan prolaz kroz cijeli pipeline (pre-steps → LLM → validate → execute).

        Args:
            question: BIRD pitanje s gold SQL-om i meta podacima.
            strategy_code: A / B / C / D — koja strategija prompt-a.
            provider: aktivni LLM provider.

        Keyword-only override-i:
            force_*: ako je dat (True/False), nadjača self._use_* config za ovaj
                jedan poziv. None znači "koristi default iz konstruktora".
                Cascade orchestrator koristi ove da pokreće različite slojeve s
                različitim flagovima bez mutiranja shared service state-a
                (koji bi se race-ao s concurrent question evaluacijama).
        """

        # Resolve effective config: per-call override > service default
        sc_enabled = force_self_consistency if force_self_consistency is not None else self._use_self_consistency
        cl_enabled = force_column_linking if force_column_linking is not None else self._use_column_linking
        ee_enabled = force_entity_extraction if force_entity_extraction is not None else self._use_entity_extraction
        vc_enabled = force_value_check if force_value_check is not None else self._use_value_check
        # Few-shot ovisi i o tome je li retriever uopće priključen
        if force_few_shot is False:
            retriever = None
        elif force_few_shot is True:
            retriever = self._few_shot_retriever  # ako nije set, ostaje None — graceful no-op
        else:
            retriever = self._few_shot_retriever

        strategy = get_strategy(strategy_code)
        logger.info(
            "benchmark.question.start",
            question_id=question.question_id,
            db_id=question.db_id,
            strategy=strategy.code,
            provider=provider.name(),
        )

        llm_ms = 0.0
        validation_ms = 0.0
        execution_ms = 0.0
        input_tokens: int | None = None
        output_tokens: int | None = None
        retry_count = 0
        predicted_sql: str | None = None
        normalized_sql: str | None = None
        blocked = False
        error_reason: str | None = None

        # Schema za ovu BIRD bazu (cached).
        # Za D strategiju koristimo verziju s sample rows (boost EX kroz
        # case sensitivity / value awareness). Ostale strategije koriste
        # običnu shemu — to je razlika koju mjerimo u eksperimentu.
        include_samples = strategy.code == "D"
        full_schema = await self._get_schema_for_db(question.db_id, with_samples=include_samples)

        # BIRD column descriptions — samo za D (kao i sample rows i evidence,
        # to su "premium kontekst" koji razdvaja D od C u eksperimentu).
        column_descriptions = None
        if strategy.code == "D":
            column_descriptions = self._get_descriptions_for_db(question.db_id)

        # ----- Pre-step 1 (D only): SCHEMA LINKING (table-level) -----
        # Identificira relevantne tablice prije glavnog SQL poziva.
        # Fokusirana shema = manje noise, češće točan join/filter.
        # Inspirirano DAIL-SQL paperom. Failsafe: ako linker padne, koristi
        # full schemu.
        schema = full_schema
        if strategy.code == "D":
            # Reset linker akumulator za ovo pitanje (column-level linking ga
            # može pomnožiti s dodatnim pozivom).
            self._last_linker_llm_ms = 0.0
            self._last_linker_input_tokens = None
            self._last_linker_output_tokens = None
            try:
                linked = await self._link_schema(
                    question=question.question,
                    full_schema=full_schema,
                    evidence=question.evidence,
                    provider=provider,
                )
                schema = filter_schema_to_tables(full_schema, linked)
                logger.info(
                    "benchmark.schema_linking.done",
                    question_id=question.question_id,
                    full_tables=len(full_schema.tables),
                    linked_tables=len(schema.tables),
                )

                # ----- Pre-step 1b (optional): COLUMN-LEVEL LINKING -----
                # DAIL-SQL fine-grained varianta: nakon table-level linking,
                # napravi dodatni LLM call koji izabire i kolone unutar tablica.
                # Smanjuje schema noise još više (vidi failure analyzer:
                # `wrong_columns` failure-i mogu pasti).
                if cl_enabled:
                    try:
                        schema = await self._link_columns(
                            question=question.question,
                            schema_after_table_linking=schema,
                            evidence=question.evidence,
                            provider=provider,
                        )
                        logger.info(
                            "benchmark.column_linking.done",
                            question_id=question.question_id,
                            tables=len(schema.tables),
                            total_cols=sum(len(t.columns) for t in schema.tables),
                        )
                    except Exception as exc:
                        logger.warning("benchmark.column_linking.failed", error=str(exc))

                # Akumuliraj linker metrike u glavne brojače
                llm_ms += self._last_linker_llm_ms
                if self._last_linker_input_tokens is not None:
                    input_tokens = (input_tokens or 0) + self._last_linker_input_tokens
                if self._last_linker_output_tokens is not None:
                    output_tokens = (output_tokens or 0) + self._last_linker_output_tokens
            except Exception as exc:
                logger.warning("benchmark.schema_linking.failed", error=str(exc))
                schema = full_schema  # failsafe

        # ----- Pre-step 2 (D only): QUESTION DECOMPOSITION -----
        # Razlaže pitanje u 2-4 koraka, daje LLM-u plan koji slijedi kad piše SQL.
        # Cilja "multiple_issues" failures iz failure analyzera.
        decomposition_text = ""
        if strategy.code == "D":
            try:
                decomposition_text = await self._decompose_question(
                    question=question.question,
                    evidence=question.evidence,
                    provider=provider,
                )
                llm_ms += self._last_decomp_llm_ms
                if self._last_decomp_input_tokens is not None:
                    input_tokens = (input_tokens or 0) + self._last_decomp_input_tokens
                if self._last_decomp_output_tokens is not None:
                    output_tokens = (output_tokens or 0) + self._last_decomp_output_tokens
                logger.info(
                    "benchmark.decomposition.done",
                    question_id=question.question_id,
                    steps_chars=len(decomposition_text),
                )
            except Exception as exc:
                logger.warning("benchmark.decomposition.failed", error=str(exc))
                decomposition_text = ""  # failsafe

        # ----- Pre-step 3 (D only, optional): ENTITY EXTRACTION + VALUE MAPPING -----
        # LLM extracta entitete iz pitanja, mi querymo DB za stvarne vrijednosti
        # (case-insensitive + prefix match). Rezultat: hint blok u prompt-u.
        # Direct attack na `wrong_filter` (case mismatch).
        value_hints_block = ""
        if strategy.code == "D" and ee_enabled:
            try:
                value_hints_block = await self._extract_and_map_entities(
                    question=question.question,
                    evidence=question.evidence,
                    schema=schema,
                    db_id=question.db_id,
                    provider=provider,
                )
                llm_ms += self._last_entity_llm_ms
                if self._last_entity_input_tokens is not None:
                    input_tokens = (input_tokens or 0) + self._last_entity_input_tokens
                if self._last_entity_output_tokens is not None:
                    output_tokens = (output_tokens or 0) + self._last_entity_output_tokens
                logger.info(
                    "benchmark.entity_extraction.done",
                    question_id=question.question_id,
                    hints_chars=len(value_hints_block),
                )
            except Exception as exc:
                logger.warning("benchmark.entity_extraction.failed", error=str(exc))

        # ----- Pre-step 4 (D only, optional): RETRIEVAL-AUGMENTED FEW-SHOT -----
        # TF-IDF retrieval iz BIRD pool-a — sličnaa pitanja s gold SQL-om
        # postaju few-shot primjeri u prompt-u (DAIL-SQL core contribution).
        few_shot_block = ""
        if strategy.code == "D" and retriever is not None:
            examples = retriever.retrieve_for(
                question=question.question,
                db_id=question.db_id,
                k=3,
                exclude_question_ids={question.question_id},
            )
            few_shot_block = format_few_shot_examples(examples)
            if few_shot_block:
                logger.info(
                    "benchmark.few_shot.retrieved",
                    question_id=question.question_id,
                    count=len(examples),
                    top_similarity=round(examples[0].similarity, 3),
                )

        # Augmentiraj pitanje s value hints i few-shot prefix-om. Strategy-evi
        # tretiraju cijelu stvar kao "question" — to nije idealno semantički,
        # ali izbjegava treburanje svih template-a za svaki novi context block.
        augmented_question = question.question
        if value_hints_block:
            augmented_question = f"{value_hints_block}\n\nQuestion: {question.question}"
        if few_shot_block:
            augmented_question = f"{few_shot_block}\n\n{augmented_question}"

        # ----- Inicijalni LLM poziv -----
        # Evidence + column descriptions + decomposition prosljeđujemo SAMO
        # za strategiju D. Ostale strategije (A/B/C) ignoriraju te parametre.
        prompt = await self._prompt_builder.build(
            question=augmented_question,
            strategy=strategy,
            dialect="sqlite",
            schema_override=schema,
            evidence=question.evidence if strategy.code == "D" else "",
            column_descriptions=column_descriptions,
            decomposition=decomposition_text,
        )
        # ----- Self-consistency mode (D only, optional): N=5 + voting -----
        # Zamjenjuje single LLM call s N paralelnih, glasa po rezultatu.
        # Najveći single boost iz literature (~+10-15 pp). Skuplji 5x na
        # glavnom step-u, pa pažljivo s budgetom.
        used_self_consistency = (
            strategy.code == "D" and sc_enabled
        )

        if used_self_consistency:
            candidates = await generate_candidates(
                prompt, provider, n_samples=self._n_consistency_samples,
            )
            # Akumuliraj metrike svih candidate poziva
            for c in candidates:
                llm_ms += c.latency_ms
                if c.input_tokens is not None:
                    input_tokens = (input_tokens or 0) + c.input_tokens
                if c.output_tokens is not None:
                    output_tokens = (output_tokens or 0) + c.output_tokens

            # Validate + execute + vote (sve unutar consistency modula)
            with Timer() as t:
                consensus = await execute_and_vote(
                    candidates=candidates,
                    validator=self._validator,
                    executor=self._executor,
                    db_id=question.db_id,
                    schema=schema,
                )
            # validation_ms + execution_ms bit će dodano nakon (out of consensus)
            consensus_overhead_ms = t.elapsed_ms

            chosen = consensus.chosen
            predicted_sql = chosen.sql
            normalized_sql = chosen.normalized_sql
            validated = chosen.validated
            executed = chosen.executed
            error_reason = chosen.error
            predicted_columns = chosen.columns
            predicted_rows = chosen.rows

            # Razdvoji consensus overhead na validation + execution proporcionalno —
            # nemamo precizne brojke, pa stavljamo sve u validation_ms (overestimate ok).
            validation_ms += consensus_overhead_ms

            logger.info(
                "benchmark.self_consistency.done",
                question_id=question.question_id,
                vote_count=consensus.vote_count,
                total_executed=consensus.total_executed,
                consensus_strength=round(consensus.consensus_strength, 2),
            )

            # Skip ostatak validation/retry path-a — consensus već donio odluku
            # Goto block na kraju koji izlazi result
        else:
            # ----- Single-call path (non self-consistency) -----
            llm_response = await provider.generate(prompt)
            llm_ms += llm_response.latency_ms
            if llm_response.input_tokens is not None:
                input_tokens = (input_tokens or 0) + llm_response.input_tokens
            if llm_response.output_tokens is not None:
                output_tokens = (output_tokens or 0) + llm_response.output_tokens
            predicted_sql = llm_response.sql

            # ----- Validacija + (D-only) retry -----
            # enforce_limit=False u benchmark mode-u: auto-LIMIT truncira korektne
            # queries koje vraćaju >1000 redaka i lažno ih označava kao
            # "wrong_result" u EX usporedbi s gold-om (koji nije limitiran).
            with Timer() as t:
                validation = await self._validator.validate(
                    predicted_sql,
                    dialect="sqlite",
                    schema_override=schema,
                    enforce_limit=False,
                )
            validation_ms += t.elapsed_ms

            if strategy.code == "D":
                while (
                    not validation.ok
                    and validation.blocked_reason is None
                    and retry_count < self._max_retry_attempts
                ):
                    retry_count += 1
                    # Retry dobiva PUN context — schema s sample rows-ima, column
                    # descriptions, evidence. Logika: ako je inicijalni poziv pao,
                    # ne želimo dodatno skriti informacije pri retry-u.
                    retry_prompt = await self._prompt_builder.build_retry(
                        question=question.question,
                        previous_sql=predicted_sql,
                        errors=validation.errors,
                        dialect="sqlite",
                        schema_override=schema,
                        column_descriptions=column_descriptions,
                        evidence=question.evidence,
                        decomposition=decomposition_text,
                    )
                    llm_response = await provider.generate(retry_prompt)
                    llm_ms += llm_response.latency_ms
                    # Kumulativni tokens (svaki retry dodaje novi LLM poziv)
                    if input_tokens is not None and llm_response.input_tokens is not None:
                        input_tokens += llm_response.input_tokens
                    if output_tokens is not None and llm_response.output_tokens is not None:
                        output_tokens += llm_response.output_tokens
                    predicted_sql = llm_response.sql

                    with Timer() as t:
                        validation = await self._validator.validate(
                            predicted_sql,
                            dialect="sqlite",
                            schema_override=schema,
                            enforce_limit=False,
                        )
                    validation_ms += t.elapsed_ms

            normalized_sql = validation.normalized_sql

            # ----- Value-aware validation (D only, optional) -----
            # Provjeri WHERE literale protiv stvarnih DB vrijednosti — hvata
            # case-mismatch failure ("North Bohemia" vs "north Bohemia").
            # Ako pronađeš warning + ima retry budgeta → retry s feedback porukom.
            # Validator je već potvrdio da kolone postoje pa parse je siguran.
            if (
                strategy.code == "D"
                and vc_enabled
                and validation.ok
                and validation.normalized_sql is not None
                and retry_count < self._max_retry_attempts
            ):
                try:
                    from app.validation.ast_checks import parse_sql
                    from app.validation.value_checks import check_where_literals

                    ast = parse_sql(validation.normalized_sql, dialect="sqlite")
                    engine = self._executor._get_engine(question.db_id)  # noqa: SLF001
                    with Timer() as t:
                        value_warnings = await check_where_literals(ast, engine)
                    validation_ms += t.elapsed_ms

                    if value_warnings:
                        logger.info(
                            "benchmark.value_check.warnings",
                            question_id=question.question_id,
                            count=len(value_warnings),
                            first=value_warnings[0][:120],
                        )
                        retry_count += 1
                        retry_prompt = await self._prompt_builder.build_retry(
                            question=question.question,
                            previous_sql=predicted_sql,
                            errors=value_warnings,
                            dialect="sqlite",
                            schema_override=schema,
                            column_descriptions=column_descriptions,
                            evidence=question.evidence,
                            decomposition=decomposition_text,
                        )
                        retry_response = await provider.generate(retry_prompt)
                        llm_ms += retry_response.latency_ms
                        if input_tokens is not None and retry_response.input_tokens is not None:
                            input_tokens += retry_response.input_tokens
                        if output_tokens is not None and retry_response.output_tokens is not None:
                            output_tokens += retry_response.output_tokens
                        predicted_sql = retry_response.sql

                        with Timer() as t:
                            validation = await self._validator.validate(
                                predicted_sql,
                                dialect="sqlite",
                                schema_override=schema,
                                enforce_limit=False,
                            )
                        validation_ms += t.elapsed_ms
                        normalized_sql = validation.normalized_sql
                except Exception as exc:
                    logger.warning("benchmark.value_check.failed", error=str(exc))

            # ----- Završetak — odluka prema validation stanju -----
            predicted_columns = []
            predicted_rows = []
            executed = False
            validated = validation.ok

            if validation.blocked_reason is not None:
                blocked = True
                error_reason = validation.blocked_reason
            elif not validation.ok:
                error_reason = "; ".join(validation.errors)
            else:
                # Validan SQL — izvrši.
                try:
                    with Timer() as t:
                        exec_result = await self._executor.execute(
                            sql=normalized_sql,
                            db_id=question.db_id,
                        )
                    execution_ms = exec_result.execution_ms
                    predicted_columns = exec_result.columns
                    predicted_rows = exec_result.rows
                    executed = True

                    # ----- Execute-then-verify (D only) -----
                    # Nakon uspješnog execute-a, provjeri je li rezultat sumnjiv
                    # (0 rows na "list", multi-row na "how many", itd.). Ako jest,
                    # pokreni jedan dodatni retry s konkretnom feedback porukom.
                    # NE računamo to kao "novi retry budžet" — ovo je separate
                    # post-execute verify retry, kontroliran posebnim brojačem.
                    if strategy.code == "D" and retry_count < self._max_retry_attempts:
                        is_suspicious, feedback = verify_result(
                            question.question,
                            exec_result.columns,
                            exec_result.rows,
                        )
                        if is_suspicious and feedback:
                            logger.info(
                                "benchmark.verify.suspicious",
                                question_id=question.question_id,
                                row_count=len(exec_result.rows),
                                feedback=feedback[:120],
                            )
                            retry_count += 1
                            # Retry s feedback porukom umjesto validator errors-a
                            retry_prompt = await self._prompt_builder.build_retry(
                                question=question.question,
                                previous_sql=predicted_sql,
                                errors=[feedback],
                                dialect="sqlite",
                                schema_override=schema,
                                column_descriptions=column_descriptions,
                                evidence=question.evidence,
                                decomposition=decomposition_text,
                            )
                            retry_response = await provider.generate(retry_prompt)
                            llm_ms += retry_response.latency_ms
                            if input_tokens is not None and retry_response.input_tokens is not None:
                                input_tokens += retry_response.input_tokens
                            if output_tokens is not None and retry_response.output_tokens is not None:
                                output_tokens += retry_response.output_tokens
                            predicted_sql = retry_response.sql

                            # Re-validate + re-execute
                            with Timer() as t:
                                validation = await self._validator.validate(
                                    predicted_sql,
                                    dialect="sqlite",
                                    schema_override=schema,
                                    enforce_limit=False,
                                )
                            validation_ms += t.elapsed_ms

                            if validation.ok and validation.normalized_sql:
                                normalized_sql = validation.normalized_sql
                                try:
                                    with Timer() as t2:
                                        new_exec = await self._executor.execute(
                                            sql=normalized_sql,
                                            db_id=question.db_id,
                                        )
                                    execution_ms += new_exec.execution_ms
                                    predicted_columns = new_exec.columns
                                    predicted_rows = new_exec.rows
                                    # executed ostaje True jer je novi run uspio
                                    logger.info(
                                        "benchmark.verify.retry.executed",
                                        question_id=question.question_id,
                                        new_row_count=len(new_exec.rows),
                                    )
                                except Exception:
                                    # Ako verify retry SQL ne izvrši se, zadržavamo
                                    # prethodni (možda manje točan) rezultat.
                                    pass
                except Exception as exc:
                    error_reason = f"exec: {exc}"
                    executed = False

        total_ms = llm_ms + validation_ms + execution_ms
        result = BenchmarkQuestionResult(
            question_id=question.question_id,
            db_id=question.db_id,
            question=question.question,
            difficulty=question.difficulty,
            strategy=strategy.code,
            provider=provider.name(),
            gold_sql=question.gold_sql,
            predicted_sql=predicted_sql,
            normalized_sql=normalized_sql,
            validated=validated,
            executed=executed,
            blocked=blocked,
            error_reason=error_reason,
            retry_count=retry_count,
            llm_ms=llm_ms,
            validation_ms=validation_ms,
            execution_ms=execution_ms,
            total_ms=total_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            predicted_columns=predicted_columns,
            predicted_rows=predicted_rows,
        )
        logger.info(
            "benchmark.question.done",
            question_id=question.question_id,
            executed=executed,
            blocked=blocked,
            retry_count=retry_count,
            total_ms=round(total_ms),
        )
        return result

    # ------------------------------------------------------------------
    # CASCADE orchestrator
    # ------------------------------------------------------------------

    async def _evaluate_cascade(
        self,
        question: BirdQuestion,
        strategy_code: str,
        provider: BaseLLMProvider,
    ) -> BenchmarkQuestionResult:
        """Cascade strategija — slojevito aktiviranje improvements po failure mode-u.

        Filozofija: pojedinačni `--all-improvements` test je pokazao da
        improvements **interferiraju** kad se kombiniraju (5 svih daje +2pp
        umjesto očekivanih +15-20pp, validation čak pada). Naivno OR-iranje
        sabotira pipeline.

        Cascade rješava to **uvjetnom aktivacijom**:
        - Layer 1: D-basic + few-shot (ako retriever postoji). Few-shot je
          uvijek-uključen jer ablation je pokazao +6pp solo s **zero**
          latency overhead (samo prompt augmentation). Pokriva ~85% pitanja.
        - Layer 2: ako validacija fail → column-linking (focused schema).
          Filozofija: invalid SQL znači LLM se izgubio u širokoj shemi;
          pruning može pomoći fokusirati ga.
        - Layer 3: ako executed ali rezultat sumnjiv (0 rows na "list",
          multi-row na "count", itd.) → self-consistency. Filozofija:
          single attempt je dao "skoro točan" odgovor; voting kroz N=5
          može hvatati raznolikost.

        Verzioniranje: ako CLI proslijedi ``few_shot_retriever``, ova
        cascade postaje "Cascade v2" (FS u Layer 1). Bez retrievera,
        ostaje "Cascade v1" (samo CL + SC slojevi). Tako jedan code path
        servira oba varijantna eksperimenta za ablation tablicu u radu.

        Što namjerno NE radimo:
        - Entity-extraction & value-check ne uključujemo automatski jer
          ablation je pokazao 0pp benefit (ne pomažu na gpt-4o-mini).

        Cost analiza:
        - Layer 1 uvijek (1x base call ≈ $0.001)
        - Layer 2 ~15% pitanja (validation_fail_rate iz baselina) ≈ +0.15x
        - Layer 3 ~20% pitanja (verify_suspicious rate, procjena) ≈ +1.0x
          (jer SC × 5 calls)
        - Očekivano ~1.3x cost basic-a (umjesto 2.6x za --all-improvements)
        """

        # Cascade v2 detection: few-shot je uključen u sve slojeve AKO je
        # retriever proslijeđen (cheap & proven boost). U layer-call-ovima
        # niže prosljeđujemo `force_few_shot=True` što znači "koristi
        # retriever ako je dat" (graceful no-op kad nije).
        fs_in_layers = self._few_shot_retriever is not None

        logger.info(
            "cascade.layer1.start",
            question_id=question.question_id,
            cascade_variant="v2" if fs_in_layers else "v1",
        )

        # ----- Layer 1: D-basic (+ few-shot ako konfiguriran) -----
        result = await self._one_pass(
            question, strategy_code, provider,
            force_self_consistency=False,
            force_column_linking=False,
            force_entity_extraction=False,
            force_value_check=False,
            force_few_shot=fs_in_layers,
        )

        # Safety-blocked → odmah vrati, nikad ne retry (može biti zlonamjeran upit)
        if result.blocked:
            logger.info("cascade.blocked.return", question_id=question.question_id)
            return result

        # ----- Layer 2: ako validacija pala → column-linking pomaže fokusirati shemu -----
        if not result.validated:
            logger.info(
                "cascade.layer2.column_linking",
                question_id=question.question_id,
                layer1_error=(result.error_reason or "")[:120],
            )
            result_cl = await self._one_pass(
                question, strategy_code, provider,
                force_self_consistency=False,
                force_column_linking=True,
                force_entity_extraction=False,
                force_value_check=False,
                force_few_shot=fs_in_layers,
            )
            # CL je uspio → preferiraj njega. Inače zadrži basic (možda ima
            # bolji error reason za debug).
            if result_cl.validated:
                logger.info(
                    "cascade.layer2.recovered",
                    question_id=question.question_id,
                )
                # Akumuliraj layer 1 trošak da metric tačno odražava cascade
                return self._merge_cascade_costs(result_cl, [result])
            return self._merge_cascade_costs(result, [result_cl])

        # ----- Layer 3: ako executed ali rezultat sumnjiv → self-consistency -----
        # Dva okidača (logical OR):
        #   1. Heuristika verify_result: 0 rows na "list", multi-row na count, itd.
        #   2. LLM-as-Judge (opcionalno, --llm-judge): semantička procjena
        #      "rezultat NE odgovara pitanju". Hvata greške koje heuristika
        #      propušta (krivu kolonu, krivu agregaciju, krivu kardinalnost).
        if result.executed:
            is_suspicious, _ = verify_result(
                question.question,
                result.predicted_columns,
                result.predicted_rows,
            )
            judge_says_wrong = False
            if self._use_llm_judge and not is_suspicious:
                # Pokreni judge samo ako heuristika već nije triggerirala —
                # nema potrebe trošiti LLM call kad već znamo da je suspicious.
                from app.services.result_judge import llm_judge_result
                try:
                    judge_says_wrong, judge_reason = await llm_judge_result(
                        question=question.question,
                        sql=result.normalized_sql or result.predicted_sql or "",
                        columns=result.predicted_columns,
                        rows=result.predicted_rows,
                        provider=provider,
                    )
                    if judge_says_wrong:
                        logger.info(
                            "cascade.layer3.judge_flagged",
                            question_id=question.question_id,
                            reason=judge_reason[:120],
                        )
                except Exception as exc:
                    logger.debug("cascade.layer3.judge_error", error=str(exc))

            if is_suspicious or judge_says_wrong:
                trigger = "verify_heuristic" if is_suspicious else "llm_judge"
                logger.info(
                    "cascade.layer3.self_consistency",
                    question_id=question.question_id,
                    row_count=len(result.predicted_rows),
                    trigger=trigger,
                )
                result_sc = await self._one_pass(
                    question, strategy_code, provider,
                    force_self_consistency=True,
                    force_column_linking=False,
                    force_entity_extraction=False,
                    force_value_check=False,
                    force_few_shot=fs_in_layers,
                )
                if result_sc.executed:
                    # SC rezultat — provjeri je li i on sumnjiv
                    is_sc_suspicious, _ = verify_result(
                        question.question,
                        result_sc.predicted_columns,
                        result_sc.predicted_rows,
                    )
                    if not is_sc_suspicious:
                        # SC dao "non-suspicious" rezultat → vjerojatno bolji
                        logger.info("cascade.layer3.sc_resolved", question_id=question.question_id)
                        return self._merge_cascade_costs(result_sc, [result])
                    # Oba sumnjiva → fallback na Layer 1 (deterministički preferirajmo basic)
                    logger.info("cascade.layer3.both_suspicious", question_id=question.question_id)
                # Ako SC nije ni executed, zadrži basic
                return self._merge_cascade_costs(result, [result_sc])

        # Layer 1 dovoljan — vrati kako jest
        return result

    @staticmethod
    def _merge_cascade_costs(
        winning: BenchmarkQuestionResult,
        also_ran: list[BenchmarkQuestionResult],
    ) -> BenchmarkQuestionResult:
        """Zbroji LLM/validation/execution troškove svih cascade slojeva u winning.

        Razlog: cascade radi 2-3 pune evaluacije po pitanju kad fail-a Layer 1.
        Metriku troška želimo prikazati POŠTENO (kao da je 1 cascade poziv =
        zbroj svih pokušaja), ne kao "samo poslijednji". Inače u radu cascade
        izgleda prejeftino što nije ispravno.
        """

        from dataclasses import replace as dataclass_replace

        extra_llm = sum(r.llm_ms for r in also_ran)
        extra_val = sum(r.validation_ms for r in also_ran)
        extra_exec = sum(r.execution_ms for r in also_ran)
        extra_in = sum((r.input_tokens or 0) for r in also_ran)
        extra_out = sum((r.output_tokens or 0) for r in also_ran)
        extra_retry = sum(r.retry_count for r in also_ran)

        new_in = (winning.input_tokens or 0) + extra_in if (winning.input_tokens is not None or extra_in) else None
        new_out = (winning.output_tokens or 0) + extra_out if (winning.output_tokens is not None or extra_out) else None

        return dataclass_replace(
            winning,
            llm_ms=winning.llm_ms + extra_llm,
            validation_ms=winning.validation_ms + extra_val,
            execution_ms=winning.execution_ms + extra_exec,
            total_ms=winning.total_ms + extra_llm + extra_val + extra_exec,
            input_tokens=new_in,
            output_tokens=new_out,
            retry_count=winning.retry_count + extra_retry,
        )

    async def _link_schema(
        self,
        question: str,
        full_schema: DatabaseSchema,
        evidence: str,
        provider: BaseLLMProvider,
    ) -> list[str]:
        """Schema linking pre-step: pita LLM koje su tablice relevantne.

        Vraća listu validnih imena tablica koje LLM smatra potrebnim.
        Spremi metrike u ``self._last_linker_*`` da ih `evaluate` može
        akumulirati u ukupne brojke.
        """

        prompt = build_schema_linking_prompt(question, full_schema, evidence)
        response = await provider.generate(prompt)
        self._last_linker_llm_ms = response.latency_ms
        self._last_linker_input_tokens = response.input_tokens
        self._last_linker_output_tokens = response.output_tokens

        available = {t.name for t in full_schema.tables}
        # Koristimo raw_text umjesto .sql jer linker ne vraća SQL —
        # vraća listu tablica (extract_sql bi mu pokušao izvući SELECT).
        linked = parse_linked_tables(response.raw_text, available)
        return linked

    async def _link_columns(
        self,
        question: str,
        schema_after_table_linking: DatabaseSchema,
        evidence: str,
        provider: BaseLLMProvider,
    ) -> DatabaseSchema:
        """Column-level linking pre-step (DAIL-SQL fine-grained variant).

        Reducira schemu još više — ne samo tablice nego i kolone unutar njih.
        Ulazi tek nakon table-level linking (manje noise = LLM lakše odredi
        kolone).

        Metrike (llm_ms, tokens) akumuliraju se u `_last_linker_*` zbrojene
        s prethodnim table-linking metrikama (caller već koristi te).
        """

        prompt = build_column_linking_prompt(question, schema_after_table_linking, evidence)
        response = await provider.generate(prompt)
        # Akumuliramo (linker metrics su zbroj table + column step-a)
        self._last_linker_llm_ms += response.latency_ms
        if response.input_tokens is not None:
            self._last_linker_input_tokens = (self._last_linker_input_tokens or 0) + response.input_tokens
        if response.output_tokens is not None:
            self._last_linker_output_tokens = (self._last_linker_output_tokens or 0) + response.output_tokens

        col_map = parse_linked_columns(response.raw_text, schema_after_table_linking)
        if not col_map:
            # Failsafe: ako parser nije izvukao ništa, vrati input schemu netaknutu
            return schema_after_table_linking
        return filter_schema_to_columns(schema_after_table_linking, col_map)

    async def _extract_and_map_entities(
        self,
        question: str,
        evidence: str,
        schema: DatabaseSchema,
        db_id: str,
        provider: BaseLLMProvider,
    ) -> str:
        """Entity extraction + value mapping → vraća text block za prompt.

        Dva sub-step-a:
        1. LLM ekstrakcija entitete iz pitanja.
        2. DB lookup za stvarne vrijednosti (case-insensitive + prefix match).

        Metrike (samo LLM step) idu u `_last_entity_*`. DB lookup nije LLM
        pa nema billing token-a.

        Returns:
            String spreman za uvrstavanje u prompt (npr. "Value lookups:..."),
            ili prazan string ako nema mappings.
        """

        # LLM step
        prompt = build_entity_extraction_prompt(question, evidence)
        response = await provider.generate(prompt)
        self._last_entity_llm_ms = response.latency_ms
        self._last_entity_input_tokens = response.input_tokens
        self._last_entity_output_tokens = response.output_tokens

        entities = parse_entities(response.raw_text)
        if not entities:
            return ""

        # DB lookup step (uses BenchmarkExecutor's engine)
        engine = self._executor._get_engine(db_id)  # noqa: SLF001 — reuse engine
        mappings = await map_entities_to_values(entities, schema, engine)
        return format_value_hints(mappings)

    def _retrieve_few_shot_examples(
        self,
        question: str,
        db_id: str,
        exclude_id: int,
        k: int = 3,
    ) -> list[FewShotExample]:
        """Dohvati top-K sličnih primjera iz pool-a (TF-IDF similarity).

        Failsafe: ako retriever nije konfiguriran, vrati prazno (ne ruši).
        """

        if self._few_shot_retriever is None:
            return []
        return self._few_shot_retriever.retrieve_for(
            question=question,
            db_id=db_id,
            k=k,
            exclude_question_ids={exclude_id},
        )

    async def _decompose_question(
        self,
        question: str,
        evidence: str,
        provider: BaseLLMProvider,
    ) -> str:
        """Question decomposition pre-step: LLM razlaže pitanje u korake.

        Vraća sanitizirani tekst decomposition steps spreman za uvrstavanje
        u glavni prompt. Prazan string znači "ne uključuj decomposition".
        """

        prompt = build_decomposition_prompt(question, evidence)
        response = await provider.generate(prompt)
        self._last_decomp_llm_ms = response.latency_ms
        self._last_decomp_input_tokens = response.input_tokens
        self._last_decomp_output_tokens = response.output_tokens

        return parse_decomposition(response.raw_text)

    def _get_descriptions_for_db(self, db_id: str) -> dict[tuple[str, str], str]:
        """Učitaj (cached) BIRD column descriptions iz CSV-ova za zadanu bazu.

        Cache je u memoriji — CSV parsing nije skup ali ima smisla izbjeći
        ga 4 strategije × N pitanja puta po jednoj bazi.
        """

        if db_id in self._descriptions_cache:
            return self._descriptions_cache[db_id]

        descs = load_column_descriptions(db_id, dataset_path=Path("/app/data/bird_mini"))
        self._descriptions_cache[db_id] = descs
        logger.info("benchmark.descriptions.loaded", db_id=db_id, count=len(descs))
        return descs

    async def _get_schema_for_db(self, db_id: str, with_samples: bool = False) -> DatabaseSchema:
        """Vraća (cached) shemu za zadanu BIRD bazu.

        Cache je razdvojen po varijanti — schema bez samples-a i ona s
        samples-ima ne miješaju se (samples bi mogli biti out-of-date ili
        suvišni). Format ključa: ``(db_id, with_samples)``.
        """

        cache_key = f"{db_id}|samples={with_samples}"
        if cache_key in self._schema_cache:
            return self._schema_cache[cache_key]

        # Reuse engine iz BenchmarkExecutor-a — schema introspection može raditi
        # nad postojećim engine-om bez novog connection pool-a.
        engine = self._executor._get_engine(db_id)  # noqa: SLF001 — namjerno za reuse
        inspector = SchemaInspector(engine=engine)
        schema = await inspector.get_schema(
            force_refresh=True, include_sample_rows=with_samples
        )
        self._schema_cache[cache_key] = schema
        return schema

    async def execute_gold(
        self,
        question: BirdQuestion,
    ) -> tuple[list[str], list[list[Any]]] | None:
        """Izvrši gold SQL i vrati rezultat — za execution accuracy usporedbu.

        Ako gold SQL ne radi (rijetko, BIRD-ovi su provjereni), vraćamo None
        i metric će tretirati pitanje kao "nemerljivo execution accuracy".
        """

        try:
            result = await self._executor.execute(question.gold_sql, db_id=question.db_id)
            return result.columns, result.rows
        except Exception as exc:
            logger.warning(
                "benchmark.gold.execute.failed",
                question_id=question.question_id,
                db_id=question.db_id,
                error=str(exc),
            )
            return None
