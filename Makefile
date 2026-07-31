PYTHON ?= python
UV ?= uv
DOCKER_PLATFORM ?= linux/amd64
DOCKER_ARCH ?= amd64
DOCKER_PYTHON_IMAGE ?= python:3.14-bookworm

.PHONY: help setup run test lint format typecheck audit check check-ui-scaling pre-commit install-hooks commit release-preview release build build-app build-linux-docker build-linux-arm64-docker build-linux-docker-all clean

help:
	@printf "Доступные цели:\n"
	@printf "  setup          Установить проект и dev-зависимости через uv\n"
	@printf "  run            Запустить GUI-приложение\n"
	@printf "  test           Запустить pytest\n"
	@printf "  lint           Запустить проверки ruff\n"
	@printf "  format         Отформатировать Python-код через ruff\n"
	@printf "  typecheck      Запустить mypy\n"
	@printf "  audit          Проверить зависимости на известные уязвимости\n"
	@printf "  check          Запустить lint, typecheck, audit и тесты\n"
	@printf "  check-ui-scaling Проверить Qt-окна при scaling 100-200%%\n"
	@printf "  pre-commit     Запустить все pre-commit хуки для всех файлов\n"
	@printf "  install-hooks  Установить git hooks pre-commit\n"
	@printf "  commit         Создать Conventional Commit через Commitizen\n"
	@printf "  release-preview Показать следующую версию без изменений\n"
	@printf "  release        Проверить проект, обновить версию и создать тег\n"
	@printf "  build          Собрать sdist и wheel\n"
	@printf "  build-app      Собрать исполняемое приложение для текущей ОС\n"
	@printf "  build-linux-docker      Собрать Linux-бинарник выбранной архитектуры\n"
	@printf "  build-linux-arm64-docker Собрать Linux ARM64-бинарник\n"
	@printf "  build-linux-docker-all  Собрать Linux-бинарники amd64 и arm64\n"
	@printf "  clean          Удалить локальные build/cache артефакты\n"

setup:
	$(UV) sync --dev

run:
	$(UV) run python -m hmg

test:
	$(UV) run pytest

lint:
	$(UV) run ruff check .

format:
	$(UV) run ruff format .
	$(UV) run ruff check . --fix

typecheck:
	$(UV) run mypy src tests scripts

audit:
	$(UV) --preview-features audit-command audit --frozen

check: lint typecheck audit test

check-ui-scaling:
	$(UV) run python scripts/check_ui_scaling.py

pre-commit:
	$(UV) run pre-commit run --all-files

install-hooks:
	$(UV) run pre-commit install --hook-type pre-commit --hook-type commit-msg

commit:
	$(UV) run cz commit

release-preview:
	$(UV) run cz bump --dry-run --yes

release: check
	$(UV) run cz bump --yes --retry

build:
	$(UV) build

build-app:
	$(UV) run pyinstaller --noconfirm --clean packaging/hosts-manager-gui.spec

build-linux-docker:
	docker buildx build \
		--platform $(DOCKER_PLATFORM) \
		--build-arg PYTHON_IMAGE=$(DOCKER_PYTHON_IMAGE) \
		--target artifact \
		--output type=local,dest=dist/docker/linux-$(DOCKER_ARCH) \
		.

build-linux-arm64-docker:
	$(MAKE) build-linux-docker \
		DOCKER_PLATFORM=linux/arm64 \
		DOCKER_ARCH=arm64 \
		DOCKER_PYTHON_IMAGE=python:3.14-trixie

build-linux-docker-all:
	docker buildx bake linux

clean:
	rm -rf .mypy_cache .pytest_cache .ruff_cache build dist *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
