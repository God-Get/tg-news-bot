# Аудит источников канала htech_plus (2026-02-19)

## Что анализировалось
- Публичная веб-лента канала: `https://t.me/s/htech_plus`
- Скан: последние `270` постов (доступные страницы на момент анализа).
- Из постов извлечены ссылки, затем отдельно проанализированы статьи `hightech.plus`, на которые канал ссылается.

## Итог по структуре ссылок
- Постов просмотрено: `270`
- Ссылок в постах: `232`
- Ссылок на `hightech.plus`: `220` (практически основной формат канала)
- Уникальных статей `hightech.plus` проанализировано: `205`
- Все статьи успешно открыты: `205/205 (HTTP 200)`

## Топ доменов-источников, которые встречаются в статьях канала
- `phys.org` — 12 статей
- `www.eurekalert.org` — 12
- `www.reuters.com` — 9
- `techcrunch.com` — 8
- `www.businessinsider.com` — 8
- `www.nature.com` — 7
- `interestingengineering.com` — 6
- `medicalxpress.com` — 6
- `www.chinadaily.com.cn` — 6
- `news.mit.edu` — 5

## Найденные первоисточники (или близко к ним)
Ниже домены, которые можно использовать как базу для «первоисточников» в вашем боте:

- Научные журналы/репозитории:
  - `www.nature.com`
  - `arxiv.org`
  - `pubmed.ncbi.nlm.nih.gov`
  - `www.sciencedirect.com`
  - `www.tandfonline.com`
  - `www.ahajournals.org`
  - `www.nejm.org`
  - `spectrum.ieee.org`

- Университеты/исследовательские центры:
  - `news.mit.edu`
  - `www.imperial.ac.uk`
  - `www.utsouthwestern.edu`
  - `news.ubc.ca`
  - `www.uclahealth.org`
  - `www.cedars-sinai.org`
  - `news.nm.org`
  - `www.tus.ac.jp`
  - `www.hse.ru`

- Официальные сайты компаний/организаций:
  - `openai.com`
  - `www.anthropic.com`
  - `research.ibm.com`
  - `www.roche.com`
  - `www.daimlertruck.com`
  - `www.nasa.gov`
  - `allenai.org`
  - `scale.com`

## Важное наблюдение
- У канала сейчас цепочка часто выглядит так:
  `Telegram post -> hightech.plus -> (media/press-release/исследование)`
- То есть «источник» часто уже вторичный (например, `phys.org`, `Reuters`, `TechCrunch`), а не сразу первоисточник (журнал, preprint, университет, официальный релиз лаборатории/компании).

## Рекомендация для вашего бота
1. Добавить два класса источников:
   - `PRIMARY` (журналы, arXiv, .edu/.gov, official labs/companies)
   - `SECONDARY_MEDIA` (медиа/агрегаторы).
2. В скоринге дать бонус `PRIMARY` и штраф `SECONDARY_MEDIA`.
3. Для каждого кандидата статьи делать шаг «source resolution»:
   - если найден только media-домен, пробовать автоматически найти ссылку на первоисточник.

## Артефакт анализа
- Сырые агрегированные данные сохранены локально в:
  - `tmp_trend_sources_htech_plus.json`

