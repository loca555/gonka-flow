# Gonka Flow

Монитор моста WGNK и торговли WGNK / USDT. Только чтение.

## Локальный запуск

```sh
python -m venv .venv
# Активируйте .venv и установите зависимости:
pip install -r requirements.txt
```

Задайте `APP_MODE=mints`, `LOAD_SEED_ARCHIVE=true`, затем запустите:

```sh
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
python -m unittest discover -s tests
```

## IPFS

```sh
python tools/build_ipfs.py --output dist-ipfs
ipfs add -r --cid-version=1 dist-ipfs
```

Откройте `/ipfs/<CID>/#trading` через шлюз своей IPFS-ноды. При повторной сборке используйте новую пустую папку.

В IPFS публикуется интерфейс. Живые данные поступают из публичного API `https://gonka-flow.onrender.com`; сервер и индексатор остаются на Render. Другой API можно указать через `--api-origin https://example.com` — он должен разрешать GET-запросы с IPFS-шлюзов через CORS.
