# Глубокий аудит источников `htech_plus` за 2025 год

Дата анализа: 2026-02-19  
Период: 2025-01-01 .. 2025-12-31 (UTC)

## Методика
- Скан публичного архива канала `https://t.me/s/htech_plus` по страницам `?before=<post_id>`.
- Отбор только постов за 2025 год по `tgme_widget_message_date`.
- Из постов выделены ссылки на `hightech.plus`.
- Для каждой статьи `hightech.plus` собраны внешние ссылки из контентного блока публикации.
- Выполнена классификация доменов: `primary`, `primary_org`, `secondary_media`, `other`.

## Сводные метрики
- Постов канала за 2025: `3585`
- Постов с любыми ссылками: `3579`
- Постов со ссылкой на `hightech.plus`: `3577`
- Уникальных статей `hightech.plus`: `2896`
- Статей `hightech.plus` с внешними источниками: `2804 / 2896`
- Уникальных внешних доменов: `533`
- Постов с первичными источниками (`primary`/`primary_org`): `458` (`12.78%`)
- Постов только со вторичными медиа-источниками: `1580`
- Постов без внешних источников (внутри статьи): `748`

## Топ доменов-источников (по охвату постов)
- `interestingengineering.com` — 484 поста
- `www.sciencedaily.com` — 193
- `newatlas.com` — 191
- `www.eurekalert.org` — 173
- `techcrunch.com` — 115
- `www.scmp.com` — 112
- `www.nature.com` — 97
- `phys.org` — 89
- `www.theverge.com` — 68
- `arstechnica.com` — 64

## Топ первоисточников/близких к первоисточникам (по охвату постов)
- `www.nature.com` — 97 постов
- `news.mit.edu` — 63
- `news.stanford.edu` — 15
- `arxiv.org` — 9
- `news.northwestern.edu` — 8
- `www.science.org` — 5
- `www.nasa.gov` — 5
- `openai.com` — 5
- `spectrum.ieee.org` — 6
- `www.cam.ac.uk` — 4
- `www.michiganmedicine.org` — 4
- `www.rutgers.edu` — 4

## Динамика по месяцам (кратко)
- Во всех месяцах 2025 доминируют вторичные медиа, особенно `interestingengineering.com`.
- Самые стабильные научные первоисточники: `www.nature.com`, `news.mit.edu`.
- Пик доли «медийных» источников заметен в августе-ноябре 2025.

## Вывод
- Контент канала в 2025 в основном строился по схеме:  
  `Telegram -> hightech.plus -> secondary media`
- Доля постов с явной опорой на первоисточники низкая: около `12.8%`.
- Для вашего бота это хороший кейс: повышать качество через приоритет `primary` источников и понижение веса `secondary_media`.

## Что добавить в бот по результатам аудита
1. Ввести отдельный вес источника `source_tier` (`primary > primary_org > secondary_media > other`).
2. При ingestion автоматически искать «глубинный source URL» внутри статьи и использовать его в скоринге.
3. Ввести hard-cap: если найден только `secondary_media`, не поднимать draft выше `EDITING` без ручного подтверждения.
4. Отдельно собрать whitelist первоисточников (Nature, MIT News, arXiv, Stanford News, Science, NASA и т.д.) и подключить как приоритетные источники.

## Артефакты
- Сырые данные: `tmp_htech_plus_2025_deep.json`
- Краткий аудит (последние посты): `docs/CHANNEL_AUDIT_htech_plus_2026-02-19.md`

