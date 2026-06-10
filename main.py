from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import threading
import time
import webbrowser

from jobson.config import Settings, load_settings
from jobson.scraper.linkedin import LinkedInScraper
from jobson.service import SearchService
from jobson.storage.factory import build_repository
from jobson.web.app import create_app
from jobson.ai_normalizer import AINormalizer
from jobson.wp_publisher import WPPublisher, load_wp_config


def configure_logging(settings: Settings) -> None:
    log_file = settings.logs_dir / "jobson.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(),
        ],
    )


def build_service(settings: Settings) -> SearchService:
    from jobson.scraper.jobbank import JobBankScraper
    from jobson.scraper.tuportalempleo import TuPortalEmpleoScraper
    repository = build_repository(settings)
    scrapers = {
        "linkedin": LinkedInScraper(settings.storage_state_path),
        "tpe":      TuPortalEmpleoScraper(),
        "jobbank":  JobBankScraper(),
    }
    return SearchService(scrapers=scrapers, repository=repository, data_dir=settings.data_dir)


def print_menu() -> str:
    print("\n" + "=" * 45)
    print("JobsOn - LinkedIn Search")
    print("=" * 45)
    print("1. LinkedIn Jobs")
    print("2. LinkedIn Feed (muro)")
    print("3. LinkedIn Mixto (jobs + feed)")
    print("4. Lanzar interfaz visual web")
    print("5. Salir")
    print("=" * 45)
    return input("Selecciona opción (1-5): ").strip()


def parse_csv_tokens(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in re.split(r"[,\n;|]+", raw) if item and item.strip()]


def run_search_sync(
    service: SearchService,
    mode: str,
    keywords: str,
    limit: int,
    days: int | None,
    exclude_keywords: list[str] | None = None,
    languages: list[str] | None = None,
    sources: list[str] | None = None,
) -> None:
    result = asyncio.run(
        service.run_search(
            keywords=keywords,
            limit=limit,
            days=days,
            sources=sources or ["linkedin"],
            linkedin_mode=mode,
            exclude_keywords=exclude_keywords or [],
            languages=languages or [],
        )
    )

    print("\n" + "!" * 60)
    print(
        f"Scraping completado: scrapeados {result['scraped_total']} | "
        f"aceptados {result['matched_total']}"
    )
    print(
        f"Scrapeados -> Jobs: {result['scraped_jobs']} | Feed: {result['scraped_feed']}"
    )
    print(
        f"Aceptados -> Jobs: {result['matched_jobs']} | Feed: {result['matched_feed']}"
    )
    print(
        "Excluidos -> "
        f"palabras: {result['filtered_out']['excluded_keywords']} | "
        f"idioma: {result['filtered_out']['language']}"
    )
    print(
        f"Persistencia -> nuevos: {result['persisted']['inserted']}, "
        f"duplicados/actualizados: {result['persisted']['updated']}"
    )
    if result.get("exclude_keywords"):
        print(f"Palabras excluyentes activas: {', '.join(result['exclude_keywords'])}")
    if result.get("languages"):
        print(f"Idiomas permitidos: {', '.join(result['languages'])}")
    if result["csv_path"]:
        print(f"CSV local: {result['csv_path']}")
        print(f"Abrir carpeta: open {os.path.dirname(result['csv_path'])}")
    print(f"Backend de datos: {result['storage_backend']}")
    print("!" * 60)


def run_cli_interactive(settings: Settings) -> None:
    service = build_service(settings)

    while True:
        choice = print_menu()
        if choice == "5":
            print("Saliendo de JobsOn.")
            return

        if choice == "4":
            launch_web(settings, auto_open=True)
            continue

        if choice not in {"1", "2", "3"}:
            print("Opción inválida.")
            continue

        keywords = input("Palabras clave: ").strip()
        if not keywords:
            print("Debes ingresar palabras clave.")
            continue

        try:
            limit = int(input("Límite de resultados (default 20): ").strip() or "20")
            days_raw = input("Antigüedad máxima en días (vacío = cualquiera): ").strip()
            days = int(days_raw) if days_raw else None
        except ValueError:
            print("Límite y días deben ser números enteros.")
            continue

        exclude_raw = input("Palabras excluyentes (coma, opcional): ").strip()
        languages_raw = input("Idiomas permitidos (coma, ej: es,en | vacío = todos): ").strip()
        exclude_keywords = parse_csv_tokens(exclude_raw)
        languages = parse_csv_tokens(languages_raw)

        mode = {"1": "jobs", "2": "feed", "3": "mixed"}[choice]
        run_search_sync(
            service,
            mode=mode,
            keywords=keywords,
            limit=limit,
            days=days,
            exclude_keywords=exclude_keywords,
            languages=languages,
        )


def run_create_admin(settings: Settings) -> None:
    import getpass

    from jobson.web.auth import hash_password

    repository = build_repository(settings)
    print(f"\n[Crear admin en backend: {repository.backend_name}]")
    email = input("Email: ").strip().lower()
    name  = input("Nombre: ").strip()
    pwd1  = getpass.getpass("Contraseña: ")
    pwd2  = getpass.getpass("Repite contraseña: ")
    if not email or not pwd1:
        raise SystemExit("Email y contraseña son obligatorios.")
    if pwd1 != pwd2:
        raise SystemExit("Las contraseñas no coinciden.")
    existing = repository.get_reviewer_by_email(email)
    if existing:
        raise SystemExit(f"Ya existe un revisor con email {email}.")
    out = repository.create_reviewer(
        email=email,
        password_hash=hash_password(pwd1),
        name=name or email,
        role="admin",
    )
    print(f"\nAdmin creado: id={out.get('id')} email={out.get('email')}\n")


def run_publish_wp(settings: Settings, args: argparse.Namespace) -> None:
    import json as _json
    from datetime import datetime as _dt

    cfg = load_wp_config()
    repository = build_repository(settings)
    ai = None if args.ai_disable else AINormalizer.from_env()
    publisher = WPPublisher(
        repository=repository,
        cfg=cfg,
        dry_run=args.dry_run,
        ai_normalizer=ai,
        ai_disabled=args.ai_disable,
        ai_force=args.ai_force,
    )

    user_filter: str | None = args.wp_filter_status
    if user_filter and user_filter.lower() == "all":
        user_filter = None

    test_batch_id: str | None = None
    manifest_path = None
    if args.wp_test and not args.dry_run:
        test_batch_id = _dt.now().strftime("%Y%m%d-%H%M%S")
        runs_dir = settings.data_dir / "wp_test_runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = runs_dir / f"{test_batch_id}.json"

    wp_filter = None if args.wp_filter == "all" else args.wp_filter
    summary = publisher.publish_batch(
        limit=max(1, args.limit),
        user_status_filter=user_filter,
        status=args.wp_status,
        source_type=args.wp_source_type,
        test_batch_id=test_batch_id,
        review_status=args.review,
        wp_status=wp_filter,
    )

    # Si se usó --review approved (o se pasó --mark-published explícito), marca como publicadas
    auto_mark = (args.review == "approved") or args.mark_published
    if auto_mark and not args.dry_run and not test_batch_id:
        for item in summary.get("items", []):
            if item.get("id") and item.get("action") in ("created", "updated"):
                try:
                    repository.mark_published(
                        dedupe_key=item["dedupe_key"],
                        post_id=int(item["id"]),
                        wp_url=item.get("link"),
                    )
                except Exception:
                    pass

    if manifest_path is not None:
        manifest = {
            "batch_id":   test_batch_id,
            "base_url":   cfg.base_url,
            "created_at": _dt.now().isoformat(),
            "items":      [i for i in summary["items"] if i.get("id")],
        }
        manifest_path.write_text(_json.dumps(manifest, indent=2, ensure_ascii=False))

    ai_status = "off" if args.ai_disable else (
        f"on ({ai.cfg.model})" if ai else "no key (cae a regex)"
    )
    print("\n" + "=" * 60)
    print(f"WP target: {cfg.base_url}")
    print(f"Filtro user_status: {user_filter or 'TODOS'}")
    print(f"Filtro source_type: {args.wp_source_type or 'TODOS'}")
    print(f"Filtro review_status: {args.review or 'TODOS'}")
    print(f"Estado destino: {args.wp_status or cfg.default_status}")
    print(f"IA normalizer: {ai_status} | force={args.ai_force}")
    print(f"Auto mark published: {auto_mark}")
    print(f"Dry-run: {args.dry_run}")
    if test_batch_id:
        print(f"TEST batch_id: {test_batch_id}")
        print(f"Manifest: {manifest_path}")
    print("-" * 60)
    print(f"Procesados: {summary['total']}")
    print(f"Creados:    {summary['created']}")
    print(f"Actualizados: {summary['updated']}")
    print(f"Descartados (portal-leak sin email corporativo): {summary.get('discarded', 0)}")
    print(f"Errores:    {len(summary['errors'])}")
    for item in summary["items"][:20]:
        if args.dry_run:
            print(f"  [dry] {item.get('title')}  (key={item.get('dedupe_key')[:10]}…)")
        else:
            print(f"  [{item.get('action')}] id={item.get('id')} {item.get('title')}")
            print(f"         → {item.get('link')}")
    if summary["errors"]:
        print("\nErrores:")
        for err in summary["errors"][:10]:
            print(f"  - {err}")
    if test_batch_id:
        print("\nPara borrar estos posts más tarde:")
        print(f"  python3 main.py --wp-cleanup-test {test_batch_id}")
    print("=" * 60)


def run_cleanup_test(settings: Settings, args: argparse.Namespace) -> None:
    import json as _json
    from pathlib import Path as _Path

    cfg = load_wp_config()
    publisher = WPPublisher(repository=build_repository(settings), cfg=cfg, dry_run=False)

    target = args.wp_cleanup_test.strip()
    runs_dir = settings.data_dir / "wp_test_runs"
    manifest_path = _Path(target) if target.endswith(".json") else runs_dir / f"{target}.json"

    if not manifest_path.exists():
        raise SystemExit(f"Manifest no encontrado: {manifest_path}")

    manifest = _json.loads(manifest_path.read_text())
    items = manifest.get("items", [])
    print(f"\nBorrando {len(items)} posts del batch '{manifest.get('batch_id')}' en {manifest.get('base_url')}...")

    result = publisher.cleanup_test_batch(items, force=True)
    print(f"Borrados: {result['deleted']}/{len(items)}")
    if result["errors"]:
        print("Errores:")
        for err in result["errors"]:
            print(f"  - {err}")
    else:
        # Renombra manifest a .cleaned.json para evitar confusión
        cleaned = manifest_path.with_suffix(".cleaned.json")
        manifest_path.rename(cleaned)
        print(f"Manifest archivado: {cleaned}")


def launch_web(settings: Settings, auto_open: bool = False, port: int | None = None) -> None:
    app = create_app(settings)
    target_port = port or settings.web_port

    if auto_open:
        def _open() -> None:
            time.sleep(1.5)
            webbrowser.open(f"http://{settings.web_host}:{target_port}")

        thread = threading.Thread(target=_open, daemon=True)
        thread.start()

    print(f"\nInterfaz visual en: http://{settings.web_host}:{target_port}")
    print("Presiona Ctrl+C para detener el servidor.\n")
    app.run(host=settings.web_host, port=target_port, debug=False)


def main() -> None:
    settings = load_settings()
    configure_logging(settings)

    parser = argparse.ArgumentParser(description="JobsOn - LinkedIn scraper + visualizador + persistencia")
    parser.add_argument("--cli", action="store_true", help="Modo CLI interactivo")
    parser.add_argument(
        "--feature", choices=["jobs", "feed", "mixed"],
        help="Modo dentro de LinkedIn (jobs|feed|mixed). Solo aplica si la fuente incluye linkedin.",
    )
    parser.add_argument(
        "--source", "--sources",
        dest="sources",
        type=str,
        default="linkedin",
        help="Fuentes a scrapear separadas por coma: linkedin, tpe, jobbank, o all (default linkedin). Ej: 'linkedin,tpe' o 'all'.",
    )
    parser.add_argument("--keywords", type=str, help="Palabras clave")
    parser.add_argument(
        "--exclude-keywords",
        type=str,
        help="Palabras excluyentes separadas por coma. Ej: senior,manager,ventas",
    )
    parser.add_argument(
        "--languages",
        type=str,
        help="Idiomas permitidos separados por coma. Ej: es,en,pt",
    )
    parser.add_argument("--limit", type=int, default=20, help="Límite de resultados")
    parser.add_argument("--days", type=int, help="Antigüedad máxima en días")
    parser.add_argument("--port", type=int, help="Puerto para interfaz web")
    parser.add_argument("--open", action="store_true", help="Abrir navegador al lanzar interfaz web")
    parser.add_argument(
        "--publish-wp",
        action="store_true",
        help="Publica resultados en WordPress (WP Job Manager). Requiere envs WP_*.",
    )
    parser.add_argument(
        "--wp-filter-status",
        type=str,
        default="me_interesa",
        help="Filtra por user_status antes de publicar (me_interesa | ya_aplique | no_me_interesa | all)",
    )
    parser.add_argument(
        "--wp-status",
        type=str,
        choices=["publish", "draft", "pending"],
        help="Override del estado del post en WP (default WP_DEFAULT_STATUS o 'draft')",
    )
    parser.add_argument(
        "--wp-source-type",
        type=str,
        choices=["jobs", "feed", "jobbank", "tpe"],
        help="Publica solo registros de este origen (recomendado: 'jobs' para ofertas estructuradas)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Muestra el payload a enviar sin postear (úsalo con --publish-wp).",
    )
    parser.add_argument(
        "--wp-test",
        action="store_true",
        help="Modo prueba: añade prefijo [JobsOn TEST] al título, marca meta _jobson_test_batch y guarda manifest en data/wp_test_runs/ para borrado posterior.",
    )
    parser.add_argument(
        "--wp-cleanup-test",
        type=str,
        help="Borra todos los posts de un test batch. Pasa el batch_id (ej. 20260512-194530) o la ruta al manifest .json.",
    )
    parser.add_argument(
        "--ai-disable",
        action="store_true",
        help="No usar IA aunque haya OPENROUTER_API_KEY; cae a normalización regex.",
    )
    parser.add_argument(
        "--ai-force",
        action="store_true",
        help="Ignora cache de IA y re-llama OpenRouter para todos los registros del batch.",
    )
    parser.add_argument(
        "--review",
        type=str,
        choices=["pending", "approved", "rejected", "published"],
        help="Filtra por review_status antes de publicar. Recomendado: 'approved'.",
    )
    parser.add_argument(
        "--wp-filter",
        type=str,
        choices=["pending", "synced", "failed", "all"],
        default="pending",
        help=(
            "Filtra qué registros enviar al WP por su wp_status actual. "
            "'pending' (default) = solo nuevos; 'synced' = re-sincronizar publicados "
            "(útil para retro-aplicar cambios como featured image); 'all' = todos."
        ),
    )
    parser.add_argument(
        "--mark-published",
        action="store_true",
        help="Tras publicar OK, marca el registro como review_status=published. Por defecto activado si usas --review approved.",
    )
    parser.add_argument(
        "--create-admin",
        action="store_true",
        help="Crea un revisor admin interactivamente y sale.",
    )

    args = parser.parse_args()

    if args.create_admin:
        run_create_admin(settings)
        return

    if args.wp_cleanup_test:
        run_cleanup_test(settings, args)
        return

    if args.publish_wp:
        run_publish_wp(settings, args)
        return

    if args.cli and not args.feature:
        run_cli_interactive(settings)
        return

    if args.feature or (args.sources and args.sources != "linkedin"):
        # Normaliza sources: "all" → ["linkedin","tpe","jobbank"]
        raw_sources = (args.sources or "linkedin").strip().lower()
        if raw_sources in {"all", "*"}:
            sources = ["linkedin", "tpe", "jobbank"]
        else:
            sources = [s.strip() for s in raw_sources.replace(";", ",").split(",") if s.strip()]
        sources = [s for s in sources if s in {"linkedin", "tpe", "jobbank"}] or ["linkedin"]

        # Keywords solo es obligatorio si linkedin está en sources
        if "linkedin" in sources and not args.keywords:
            raise SystemExit("LinkedIn requiere --keywords. Quita linkedin de --source o pasa --keywords.")

        service = build_service(settings)
        run_search_sync(
            service,
            mode=args.feature or "mixed",
            keywords=args.keywords or "",
            limit=max(1, args.limit),
            days=args.days,
            exclude_keywords=parse_csv_tokens(args.exclude_keywords),
            languages=parse_csv_tokens(args.languages),
            sources=sources,
        )
        return

    launch_web(settings, auto_open=args.open, port=args.port)


if __name__ == "__main__":
    main()
