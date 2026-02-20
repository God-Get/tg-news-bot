# Whitelist первоисточников (на основе аудита `htech_plus` за 2025)

Дата: 2026-02-19  
Источник выбора: `tmp_htech_plus_2025_deep.json` + проверка доступности RSS/Atom (HTTP 200, feed entries > 0).

## Проверенные источники (готово к добавлению в бот)

| Source | Feed URL | Entries | Упоминаний в постах 2025 |
|---|---|---:|---:|
| Nature | `https://www.nature.com/nature.rss` | 75 | 97 |
| Nature Astronomy | `https://www.nature.com/subjects/astronomy-and-astrophysics.rss` | 30 | 97 (домен `nature.com`) |
| MIT News | `https://news.mit.edu/rss/feed` | 50 | 63 |
| MIT News: AI topic | `https://news.mit.edu/topic/mitartificial-intelligence2-rss.xml` | 50 | 63 (домен `news.mit.edu`) |
| arXiv cs.AI | `https://rss.arxiv.org/rss/cs.AI` | 225 | 9 |
| arXiv astro-ph | `https://rss.arxiv.org/rss/astro-ph` | 105 | 9 (домен `arxiv.org`) |
| arXiv physics.app-ph | `https://rss.arxiv.org/rss/physics.app-ph` | 6 | 9 (домен `arxiv.org`) |
| NASA News Release | `https://www.nasa.gov/news-release/feed/` | 10 | 5 |
| OpenAI News | `https://openai.com/news/rss.xml` | 849 | 5 |
| Science (AAAS TOC) | `https://www.science.org/action/showFeed?jc=science&type=etoc&feed=rss` | 41 | 5 |
| Northwestern Now | `https://news.northwestern.edu/feeds/allStories` | 25 | 8 |
| University of Cambridge News | `https://www.cam.ac.uk/news/feed` | 15 | 4 |
| Illinois News Bureau | `https://news.illinois.edu/feed/` | 12 | 2 |
| UC Irvine News | `https://news.uci.edu/feed/` | 50 | 2 |
| CU Boulder Today | `https://www.colorado.edu/today/rss.xml` | 10 | 2 |
| Caltech News | `https://www.caltech.edu/about/news/rss` | 12 | 1 |
| WashU Medicine | `https://medicine.washu.edu/feed/` | 10 | 3 |
| Weill Cornell Newsroom | `https://news.weill.cornell.edu/rss.xml` | 10 | 3 |
| UCLA Newsroom | `https://newsroom.ucla.edu/rss.xml` | 20 | 3 |
| CMU SCS News | `https://www.cs.cmu.edu/news/feed` | 20 | 2 |
| UConn Today | `https://today.uconn.edu/feed/` | 20 | 2 |
| Vanderbilt Health News | `https://news.vumc.org/feed/` | 20 | 2 |

## Дополнительно (качественный secondary, если нужен)

| Source | Feed URL | Entries | Упоминаний в постах 2025 |
|---|---|---:|---:|
| IEEE Spectrum | `https://spectrum.ieee.org/rss/fulltext` | 30 | 6 |

## Готовые команды `/add_source`

```text
/add_source https://www.nature.com/nature.rss | Nature
/add_source https://www.nature.com/subjects/astronomy-and-astrophysics.rss | Nature Astronomy
/add_source https://news.mit.edu/rss/feed | MIT News
/add_source https://news.mit.edu/topic/mitartificial-intelligence2-rss.xml | MIT News AI
/add_source https://rss.arxiv.org/rss/cs.AI | arXiv cs.AI
/add_source https://rss.arxiv.org/rss/astro-ph | arXiv astro-ph
/add_source https://rss.arxiv.org/rss/physics.app-ph | arXiv physics.app-ph
/add_source https://www.nasa.gov/news-release/feed/ | NASA News
/add_source https://openai.com/news/rss.xml | OpenAI News
/add_source https://www.science.org/action/showFeed?jc=science&type=etoc&feed=rss | Science Journal TOC
/add_source https://news.northwestern.edu/feeds/allStories | Northwestern Now
/add_source https://www.cam.ac.uk/news/feed | Cambridge News
/add_source https://news.illinois.edu/feed/ | Illinois News Bureau
/add_source https://news.uci.edu/feed/ | UC Irvine News
/add_source https://www.colorado.edu/today/rss.xml | CU Boulder Today
/add_source https://www.caltech.edu/about/news/rss | Caltech News
/add_source https://medicine.washu.edu/feed/ | WashU Medicine
/add_source https://news.weill.cornell.edu/rss.xml | Weill Cornell Newsroom
/add_source https://newsroom.ucla.edu/rss.xml | UCLA Newsroom
/add_source https://www.cs.cmu.edu/news/feed | CMU SCS News
/add_source https://today.uconn.edu/feed/ | UConn Today
/add_source https://news.vumc.org/feed/ | Vanderbilt Health News
```

## Рекомендованный порядок внедрения
1. Добавить сначала 8-10 самых устойчивых источников: Nature, MIT, arXiv, NASA, OpenAI, Science.
2. Проверить ingestion 24-48 часов (`/ingest_now`, `/source_quality`).
3. Добавить университетские newsroom-источники второй волной.
4. После стабилизации поднять trust-score для первоисточников.

