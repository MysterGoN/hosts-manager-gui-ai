PYTHON ?= python
UV ?= uv

.PHONY: help setup run test lint format typecheck check pre-commit install-hooks build clean

help:
	@printf "Доступные цели:\n"
	@printf "  setup          Установить проект и dev-зависимости через uv\n"
	@printf "  run            Запустить GUI-приложение\n"
	@printf "  test           Запустить pytest\n"
	@printf "  lint           Запустить проверки ruff\n"
	@printf "  format         Отформатировать Python-код через ruff\n"
	@printf "  typecheck      Запустить mypy\n"
	@printf "  check          Запустить lint, typecheck и тесты\n"
	@printf "  pre-commit     Запустить все pre-commit хуки для всех файлов\n"
	@printf "  install-hooks  Установить git hooks pre-commit\n"
	@printf "  build          Собрать sdist и wheel\n"
	@printf "  clean          Удалить локальные build/cache артефакты\n"

setup:
	$(UV) sync --dev

run:
	$(UV) run python -m hosts_manager_gui

test:
	$(UV) run pytest

lint:
	$(UV) run ruff check .

format:
	$(UV) run ruff format .
	$(UV) run ruff check . --fix

typecheck:
	$(UV) run mypy src tests

check: lint typecheck test

pre-commit:
	$(UV) run pre-commit run --all-files

install-hooks:
	$(UV) run pre-commit install

build:
	$(UV) build

clean:
	rm -rf .mypy_cache .pytest_cache .ruff_cache build dist *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
