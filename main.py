from __future__ import annotations

import argparse
import asyncio
import logging
import os
import threading
import time
import webbrowser

from jobson.config import Settings, load_settings
from jobson.scraper.linkedin import LinkedInScraper
from jobson.service import SearchService
from jobson.storage.factory import build_repository
from jobson.web.app import create_app


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
    repository = build_repository(settings)
    scraper = LinkedInScraper(settings.storage_state_path)
    return SearchService(scraper=scraper, repository=repository, data_dir=settings.data_dir)


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


def run_search_sync(service: SearchService, mode: str, keywords: str, limit: int, days: int | None) -> None:
    result = asyncio.run(service.run_search(mode=mode, keywords=keywords, limit=limit, days=days))

    print("\n" + "!" * 60)
    print(f"Scraping completado: {result['scraped_total']} registros")
    print(f"Jobs: {result['scraped_jobs']} | Feed: {result['scraped_feed']}")
    print(
        f"Persistencia -> nuevos: {result['persisted']['inserted']}, "
        f"duplicados/actualizados: {result['persisted']['updated']}"
    )
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

        mode = {"1": "jobs", "2": "feed", "3": "mixed"}[choice]
        run_search_sync(service, mode=mode, keywords=keywords, limit=limit, days=days)


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
    parser.add_argument("--feature", choices=["jobs", "feed", "mixed"], help="Modo de búsqueda")
    parser.add_argument("--keywords", type=str, help="Palabras clave")
    parser.add_argument("--limit", type=int, default=20, help="Límite de resultados")
    parser.add_argument("--days", type=int, help="Antigüedad máxima en días")
    parser.add_argument("--port", type=int, help="Puerto para interfaz web")
    parser.add_argument("--open", action="store_true", help="Abrir navegador al lanzar interfaz web")

    args = parser.parse_args()

    if args.cli and not args.feature:
        run_cli_interactive(settings)
        return

    if args.feature:
        if not args.keywords:
            raise SystemExit("Debes usar --keywords cuando ejecutas --feature")
        service = build_service(settings)
        run_search_sync(
            service,
            mode=args.feature,
            keywords=args.keywords,
            limit=max(1, args.limit),
            days=args.days,
        )
        return

    launch_web(settings, auto_open=args.open, port=args.port)


if __name__ == "__main__":
    main()
