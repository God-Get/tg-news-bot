@echo off
setlocal enabledelayedexpansion

set PROJECT_DIR=tg_news_bot

if exist "%PROJECT_DIR%" (
  echo Folder "%PROJECT_DIR%" already exists. Delete it and rerun.
  exit /b 1
)

mkdir "%PROJECT_DIR%"
cd /d "%PROJECT_DIR%"

mkdir app
mkdir app\bot
mkdir app\pipeline
mkdir app\storage
mkdir app\utils
mkdir data

REM =========================
REM requirements.txt
REM =========================
powershell -NoProfile -Command ^
  "$lines=@(" ^
  "'aiogram==3.4.1'," ^
  "'aiohttp==3.9.5'," ^
  "'python-dotenv==1.0.1'," ^
  "'feedparser==6.0.11'," ^
  "'trafilatura==1.9.0'," ^
  "'beautifulsoup4==4.12.3'," ^
  "'SQLAlchemy==2.0.30'," ^
  "'aiosqlite==0.20.0'" ^
  "); Set-Content -Encoding UTF8 -Path 'requirements.txt' -Value $lines"

REM =========================
REM .env.example
REM =========================
powershell -NoProfile -Command ^
  "$lines=@(" ^
  "'# Telegram Bot token from @BotFather'," ^
  "'BOT_TOKEN=PASTE_YOUR_TOKEN_HERE'," ^
  "''" ^
  "'# SQLite in Docker container volume'," ^
  "'DATABASE_URL=sqlite+aiosqlite:////app/data/data.db'," ^
  "''" ^
  "'FETCH_LIMIT=10'," ^
  "'MIN_TEXT_LEN=1200'," ^
  "''" ^
  "'# Optional: allow only these domains (comma-separated). Empty = allow all.'," ^
  "'WHITELIST_DOMAINS='," ^
  "''" ^
  "'# Optional: block these domains (comma-separated)'," ^
  "'BLACKLIST_DOMAINS=medium.com,towardsdatascience.com'" ^
  "); Set-Content -Encoding UTF8 -Path '.env.example' -Value $lines"

REM =========================
REM README.md
REM =========================
powershell -NoProfile -Command ^
  "$lines=@(" ^
  "'# TG News Bot (Stage 1: RSS -> Extract -> Draft EN -> INBOX)'," ^
  "''" ^
  "'## Run with Docker'," ^
  "'1) Copy .env.example -> .env and set BOT_TOKEN'," ^
  "'2) docker compose up --build'," ^
  "''" ^
  "'## Telegram setup'," ^
  "'- Create a supergroup, enable Topics (Forum)'," ^
  "'- Create topic: Входящие'," ^
  "'- Add bot as admin (at least: send messages)'," ^
  "''" ^
  "'## Commands'," ^
  "'/set_group — save current chat as working group'," ^
  "'/set_inbox_topic — save current topic as INBOX'," ^
  "'/add_source <rss_url> — add RSS source'," ^
  "'/list_sources — list sources'," ^
  "'/fetch — fetch & process (Stage 1: EN only)'," ^
  "'(Reject button works: deletes card + marks draft REJECTED)'" ^
  "); Set-Content -Encoding UTF8 -Path 'README.md' -Value $lines"

REM =========================
REM Dockerfile
REM =========================
powershell -NoProfile -Command ^
  "$lines=@(" ^
  "'FROM python:3.11-slim'," ^
  "''" ^
  "'ENV PYTHONDONTWRITEBYTECODE=1 \\'," ^
  "'    PYTHONUNBUFFERED=1'," ^
  "''" ^
  "'WORKDIR /app'," ^
  "''" ^
  "'RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*'," ^
  "''" ^
  "'COPY requirements.txt /app/requirements.txt'," ^
  "'RUN pip install --no-cache-dir -r /app/requirements.txt'," ^
  "''" ^
  "'COPY app /app/app'," ^
  "'RUN mkdir -p /app/data'," ^
  "''" ^
  "'CMD [\"\"python\"\", \"\"-m\"\", \"\"app.main\"\"]'" ^
  "); Set-Content -Encoding UTF8 -Path 'Dockerfile' -Value $lines"

REM =========================
REM docker-compose.yml
REM =========================
powershell -NoProfile -Command ^
  "$lines=@(" ^
  "'services:'," ^
  "'  bot:'," ^
  "'    build: .'," ^
  "'    container_name: tg_news_bot'," ^
  "'    restart: unless-stopped'," ^
  "'    env_file:'," ^
  "'      - .env'," ^
  "'    volumes:'," ^
  "'      - ./data:/app/data'" ^
  "); Set-Content -Encoding UTF8 -Path 'docker-compose.yml' -Value $lines"

REM =========================
REM .dockerignore
REM =========================
powershell -NoProfile -Command ^
  "$lines=@(" ^
  "'.venv'," ^
  "'__pycache__'," ^
  "'*.pyc'," ^
  "'*.pyo'," ^
  "'*.pyd'," ^
  "'data/'," ^
  "'.git'," ^
  "'.idea'," ^
  "'.vscode'" ^
  "); Set-Content -Encoding UTF8 -Path '.dockerignore' -Value $lines"

REM =========================
REM app/__init__.py
REM =========================
powershell -NoProfile -Command ^
  "Set-Content -Encoding UTF8 -Path 'app\__init__.py' -Value @('')"

REM =========================
REM app/config.py
REM =========================
powershell -NoProfile -Command ^
  "$lines=@(" ^
  "'import os'," ^
  "'from dataclasses import dataclass'," ^
  "'from dotenv import load_dotenv'," ^
  "''" ^
  "'load_dotenv()'," ^
  "''" ^
  "'def _split_csv(value: str) -> list[str]:'," ^
  "'    value = (value or \"\").strip()'," ^
  "'    if not value:'," ^
  "'        return []'," ^
  "'    return [x.strip().lower() for x in value.split(\",\") if x.strip()]'," ^
  "''" ^
  "'@dataclass'," ^
  "'class Settings:'," ^
  "'    bot_token: str'," ^
  "'    database_url: str'," ^
  "'    fetch_limit: int'," ^
  "'    min_text_len: int'," ^
  "'    whitelist_domains: list[str]'," ^
  "'    blacklist_domains: list[str]'," ^
  "''" ^
  "'def load_settings() -> Settings:'," ^
  "'    bot_token = os.getenv(\"BOT_TOKEN\", \"\").strip()'," ^
  "'    if not bot_token or bot_token == \"PASTE_YOUR_TOKEN_HERE\":'," ^
  "'        raise RuntimeError(\"BOT_TOKEN is not set. Copy .env.example to .env and set BOT_TOKEN.\")'," ^
  "''" ^
  "'    database_url = os.getenv(\"DATABASE_URL\", \"sqlite+aiosqlite:////app/data/data.db\").strip()'," ^
  "'    fetch_limit = int(os.getenv(\"FETCH_LIMIT\", \"10\"))'," ^
  "'    min_text_len = int(os.getenv(\"MIN_TEXT_LEN\", \"1200\"))'," ^
  "'    whitelist_domains = _split_csv(os.getenv(\"WHITELIST_DOMAINS\", \"\"))'," ^
  "'    blacklist_domains = _split_csv(os.getenv(\"BLACKLIST_DOMAINS\", \"medium.com,towardsdatascience.com\"))'," ^
  "''" ^
  "'    return Settings(', " ^
  "'        bot_token=bot_token,'," ^
  "'        database_url=database_url,'," ^
  "'        fetch_limit=fetch_limit,'," ^
  "'        min_text_len=min_text_len,'," ^
  "'        whitelist_domains=whitelist_domains,'," ^
  "'        blacklist_domains=blacklist_domains'," ^
  "'    )'" ^
  "); Set-Content -Encoding UTF8 -Path 'app\config.py' -Value $lines"

REM =========================
REM app/main.py
REM =========================
powershell -NoProfile -Command ^
  "$lines=@(" ^
  "'import asyncio'," ^
  "'import logging'," ^
  "''" ^
  "'from aiogram import Bot, Dispatcher'," ^
  "'from aiogram.enums import ParseMode'," ^
  "''" ^
  "'from app.config import load_settings'," ^
  "'from app.storage.db import create_engine_and_session, init_db'," ^
  "'from app.bot.dispatcher import build_router'," ^
  "''" ^
  "'logging.basicConfig(level=logging.INFO)'," ^
  "''" ^
  "'async def main():'," ^
  "'    settings = load_settings()'," ^
  "'    engine, sessionmaker = create_engine_and_session(settings.database_url)'," ^
  "'    await init_db(engine)'," ^
  "'    bot = Bot(token=settings.bot_token, parse_mode=ParseMode.HTML)'," ^
  "'    dp = Dispatcher()'," ^
  "'    dp.include_router(build_router(sessionmaker, settings))'," ^
  "'    logging.info(\"Bot started\")'," ^
  "'    await dp.start_polling(bot)'," ^
  "''" ^
  "'if __name__ == \"__main__\":'," ^
  "'    asyncio.run(main())'" ^
  "); Set-Content -Encoding UTF8 -Path 'app\main.py' -Value $lines"

REM =========================
REM app/utils/url.py
REM =========================
powershell -NoProfile -Command ^
  "$lines=@(" ^
  "'from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode'," ^
  "''" ^
  "'TRACKING_PARAMS = {'," ^
  "'    \"utm_source\",\"utm_medium\",\"utm_campaign\",\"utm_term\",\"utm_content\",'," ^
  "'    \"gclid\",\"fbclid\",\"yclid\",\"mc_cid\",\"mc_eid\"'," ^
  "'}'," ^
  "''" ^
  "'def normalize_url(url: str) -> str:'," ^
  "'    url = (url or \"\").strip()'," ^
  "'    if not url:'," ^
  "'        return url'," ^
  "'    p = urlparse(url)'," ^
  "'    qs = [(k,v) for (k,v) in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in TRACKING_PARAMS]'," ^
  "'    new_query = urlencode(qs, doseq=True)'," ^
  "'    cleaned = p._replace(query=new_query, fragment=\"\")'," ^
  "'    return urlunparse(cleaned)'," ^
  "''" ^
  "'def get_domain(url: str) -> str:'," ^
  "'    return (urlparse(url).netloc or \"\").lower()'" ^
  "); Set-Content -Encoding UTF8 -Path 'app\utils\url.py' -Value $lines"

REM =========================
REM app/utils/text.py
REM =========================
powershell -NoProfile -Command ^
  "$lines=@(" ^
  "'import hashlib'," ^
  "''" ^
  "'def sha256_hex(s: str) -> str:'," ^
  "'    return hashlib.sha256((s or \"\").encode(\"utf-8\", errors=\"ignore\")).hexdigest()'," ^
  "''" ^
  "'def clip_text(text: str, max_chars: int = 650) -> str:'," ^
  "'    text = (text or \"\").strip()'," ^
  "'    if len(text) <= max_chars:'," ^
  "'        return text'," ^
  "'    return text[:max_chars].rstrip() + \"…\"'" ^
  "); Set-Content -Encoding UTF8 -Path 'app\utils\text.py' -Value $lines"

REM =========================
REM app/storage/db.py
REM =========================
powershell -NoProfile -Command ^
  "$lines=@(" ^
  "'from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncEngine'," ^
  "'from app.storage.models import Base'," ^
  "''" ^
  "'def create_engine_and_session(database_url: str):'," ^
  "'    engine = create_async_engine(database_url, echo=False, future=True)'," ^
  "'    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)'," ^
  "'    return engine, sessionmaker'," ^
  "''" ^
  "'async def init_db(engine: AsyncEngine):'," ^
  "'    async with engine.begin() as conn:'," ^
  "'        await conn.run_sync(Base.metadata.create_all)'" ^
  "); Set-Content -Encoding UTF8 -Path 'app\storage\db.py' -Value $lines"

REM =========================
REM app/storage/models.py
REM =========================
powershell -NoProfile -Command ^
  "$lines=@(" ^
  "'from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column'," ^
  "'from sqlalchemy import String, Integer, BigInteger, Boolean, DateTime, Text, func, UniqueConstraint'," ^
  "''" ^
  "'class Base(DeclarativeBase):'," ^
  "'    pass'," ^
  "''" ^
  "'class BotSettings(Base):'," ^
  "'    __tablename__ = \"bot_settings\"'," ^
  "'    id: Mapped[int] = mapped_column(Integer, primary_key=True)'," ^
  "'    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())'," ^
  "'    updated_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())'," ^
  "''" ^
  "'    group_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)'," ^
  "'    inbox_topic_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)'," ^
  "'    fetch_limit: Mapped[int] = mapped_column(Integer, default=10)'," ^
  "''" ^
  "'class Source(Base):'," ^
  "'    __tablename__ = \"sources\"'," ^
  "'    id: Mapped[int] = mapped_column(Integer, primary_key=True)'," ^
  "'    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())'," ^
  "'    updated_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())'," ^
  "'    url: Mapped[str] = mapped_column(Text, nullable=False)'," ^
  "'    enabled: Mapped[bool] = mapped_column(Boolean, default=True)'," ^
  "''" ^
  "'class Draft(Base):'," ^
  "'    __tablename__ = \"drafts\"'," ^
  "'    __table_args__ = (UniqueConstraint(\"normalized_url_hash\", name=\"uq_drafts_normalized_url_hash\"),)'," ^
  "'    id: Mapped[int] = mapped_column(Integer, primary_key=True)'," ^
  "'    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())'," ^
  "'    updated_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())'," ^
  "''" ^
  "'    source_url: Mapped[str] = mapped_column(Text, nullable=False)'," ^
  "'    normalized_url: Mapped[str] = mapped_column(Text, nullable=False)'," ^
  "'    normalized_url_hash: Mapped[str] = mapped_column(String(64), nullable=False)'," ^
  "''" ^
  "'    title_en: Mapped[str | None] = mapped_column(Text, nullable=True)'," ^
  "'    excerpt_en: Mapped[str | None] = mapped_column(Text, nullable=True)'," ^
  "'    post_text: Mapped[str] = mapped_column(Text, nullable=False)'," ^
  "''" ^
  "'    source_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)'," ^
  "'    tg_image_file_id: Mapped[str | None] = mapped_column(Text, nullable=True)'," ^
  "'    has_image: Mapped[bool] = mapped_column(Boolean, default=False)'," ^
  "''" ^
  "'    state: Mapped[str] = mapped_column(String(20), default=\"INBOX\")'," ^
  "'    card_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)'," ^
  "'    card_topic_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)'," ^
  "'    card_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)'" ^
  "); Set-Content -Encoding UTF8 -Path 'app\storage\models.py' -Value $lines"

REM =========================
REM app/storage/repo.py
REM =========================
powershell -NoProfile -Command ^
  "$lines=@(" ^
  "'from sqlalchemy import select'," ^
  "'from sqlalchemy.ext.asyncio import async_sessionmaker'," ^
  "'from app.storage.models import BotSettings, Source, Draft'," ^
  "''" ^
  "'async def get_or_create_settings(sessionmaker: async_sessionmaker) -> BotSettings:'," ^
  "'    async with sessionmaker() as s:'," ^
  "'        res = await s.execute(select(BotSettings).limit(1))'," ^
  "'        row = res.scalar_one_or_none()'," ^
  "'        if row: return row'," ^
  "'        st = BotSettings(fetch_limit=10)'," ^
  "'        s.add(st)'," ^
  "'        await s.commit()'," ^
  "'        await s.refresh(st)'," ^
  "'        return st'," ^
  "''" ^
  "'async def update_settings(sessionmaker: async_sessionmaker, **kwargs) -> BotSettings:'," ^
  "'    async with sessionmaker() as s:'," ^
  "'        res = await s.execute(select(BotSettings).limit(1))'," ^
  "'        st = res.scalar_one()'," ^
  "'        for k,v in kwargs.items(): setattr(st,k,v)'," ^
  "'        await s.commit()'," ^
  "'        await s.refresh(st)'," ^
  "'        return st'," ^
  "''" ^
  "'async def add_source(sessionmaker: async_sessionmaker, url: str) -> Source:'," ^
  "'    async with sessionmaker() as s:'," ^
  "'        src = Source(url=url.strip(), enabled=True)'," ^
  "'        s.add(src)'," ^
  "'        await s.commit()'," ^
  "'        await s.refresh(src)'," ^
  "'        return src'," ^
  "''" ^
  "'async def list_sources(sessionmaker: async_sessionmaker) -> list[Source]:'," ^
  "'    async with sessionmaker() as s:'," ^
  "'        res = await s.execute(select(Source).order_by(Source.id.asc()))'," ^
  "'        return list(res.scalars().all())'," ^
  "''" ^
  "'async def set_source_enabled(sessionmaker: async_sessionmaker, source_id: int, enabled: bool) -> bool:'," ^
  "'    async with sessionmaker() as s:'," ^
  "'        res = await s.execute(select(Source).where(Source.id==source_id))'," ^
  "'        src = res.scalar_one_or_none()'," ^
  "'        if not src: return False'," ^
  "'        src.enabled = enabled'," ^
  "'        await s.commit()'," ^
  "'        return True'," ^
  "''" ^
  "'async def draft_exists_by_hash(sessionmaker: async_sessionmaker, normalized_url_hash: str) -> bool:'," ^
  "'    async with sessionmaker() as s:'," ^
  "'        res = await s.execute(select(Draft.id).where(Draft.normalized_url_hash==normalized_url_hash))'," ^
  "'        return res.first() is not None'," ^
  "''" ^
  "'async def insert_draft(sessionmaker: async_sessionmaker, draft: Draft) -> Draft:'," ^
  "'    async with sessionmaker() as s:'," ^
  "'        s.add(draft)'," ^
  "'        await s.commit()'," ^
  "'        await s.refresh(draft)'," ^
  "'        return draft'," ^
  "''" ^
  "'async def attach_card(sessionmaker: async_sessionmaker, draft_id: int, chat_id: int, topic_id: int, message_id: int):'," ^
  "'    async with sessionmaker() as s:'," ^
  "'        res = await s.execute(select(Draft).where(Draft.id==draft_id))'," ^
  "'        d = res.scalar_one()'," ^
  "'        d.card_chat_id = chat_id'," ^
  "'        d.card_topic_id = topic_id'," ^
  "'        d.card_message_id = message_id'," ^
  "'        await s.commit()'," ^
  "''" ^
  "'async def set_draft_state(sessionmaker: async_sessionmaker, draft_id: int, state: str):'," ^
  "'    async with sessionmaker() as s:'," ^
  "'        res = await s.execute(select(Draft).where(Draft.id==draft_id))'," ^
  "'        d = res.scalar_one()'," ^
  "'        d.state = state'," ^
  "'        await s.commit()'" ^
  "); Set-Content -Encoding UTF8 -Path 'app\storage\repo.py' -Value $lines"

REM =========================
REM app/pipeline/fetcher.py
REM =========================
powershell -NoProfile -Command ^
  "$lines=@(" ^
  "'import feedparser'," ^
  "'from dataclasses import dataclass'," ^
  "''" ^
  "'@dataclass'," ^
  "'class FeedItem:'," ^
  "'    url: str'," ^
  "'    title: str | None'," ^
  "''" ^
  "'def fetch_rss_items(rss_url: str, limit: int = 10) -> list[FeedItem]:'," ^
  "'    d = feedparser.parse(rss_url)'," ^
  "'    items: list[FeedItem] = []'," ^
  "'    for e in (d.entries or [])[:limit]:'," ^
  "'        link = getattr(e, \"link\", None)'," ^
  "'        title = getattr(e, \"title\", None)'," ^
  "'        if link: items.append(FeedItem(url=str(link), title=str(title) if title else None))'," ^
  "'    return items'" ^
  "); Set-Content -Encoding UTF8 -Path 'app\pipeline\fetcher.py' -Value $lines"

REM =========================
REM app/pipeline/extractor.py
REM =========================
powershell -NoProfile -Command ^
  "$lines=@(" ^
  "'import aiohttp'," ^
  "'import trafilatura'," ^
  "'from bs4 import BeautifulSoup'," ^
  "''" ^
  "'async def download_html(url: str, timeout_s: int = 12) -> str:'," ^
  "'    timeout = aiohttp.ClientTimeout(total=timeout_s)'," ^
  "'    async with aiohttp.ClientSession(timeout=timeout) as session:'," ^
  "'        async with session.get(url, headers={\"User-Agent\":\"Mozilla/5.0\"}) as resp:'," ^
  "'            resp.raise_for_status()'," ^
  "'            return await resp.text(errors=\"ignore\")'," ^
  "''" ^
  "'def extract_text(html: str) -> str:'," ^
  "'    text = trafilatura.extract(html, include_comments=False, include_tables=False)'," ^
  "'    return (text or \"\").strip()'," ^
  "''" ^
  "'def pick_image_url(html: str) -> str | None:'," ^
  "'    soup = BeautifulSoup(html, \"html.parser\")'," ^
  "'    def meta_prop(p: str):'," ^
  "'        t = soup.find(\"meta\", attrs={\"property\": p})'," ^
  "'        return t.get(\"content\").strip() if t and t.get(\"content\") else None'," ^
  "'    def meta_name(n: str):'," ^
  "'        t = soup.find(\"meta\", attrs={\"name\": n})'," ^
  "'        return t.get(\"content\").strip() if t and t.get(\"content\") else None'," ^
  "'    def link_rel(r: str):'," ^
  "'        t = soup.find(\"link\", attrs={\"rel\": r})'," ^
  "'        return t.get(\"href\").strip() if t and t.get(\"href\") else None'," ^
  "'    return meta_prop(\"og:image\") or meta_name(\"twitter:image\") or link_rel(\"image_src\")'" ^
  "); Set-Content -Encoding UTF8 -Path 'app\pipeline\extractor.py' -Value $lines"

REM =========================
REM app/pipeline/filtering.py
REM =========================
powershell -NoProfile -Command ^
  "$lines=@(" ^
  "'from app.utils.url import get_domain'," ^
  "''" ^
  "'def domain_allowed(url: str, whitelist: list[str], blacklist: list[str]) -> bool:'," ^
  "'    dom = get_domain(url)'," ^
  "'    if blacklist and dom in blacklist: return False'," ^
  "'    if whitelist and dom not in whitelist: return False'," ^
  "'    return True'," ^
  "''" ^
  "'def passes_min_len(text: str, min_len: int) -> bool:'," ^
  "'    return len((text or \"\").strip()) >= min_len'" ^
  "); Set-Content -Encoding UTF8 -Path 'app\pipeline\filtering.py' -Value $lines"

REM =========================
REM app/bot/dispatcher.py
REM =========================
powershell -NoProfile -Command ^
  "$lines=@(" ^
  "'from aiogram import Router'," ^
  "'from app.bot.commands import router as commands_router'," ^
  "''" ^
  "'def build_router(sessionmaker, settings):'," ^
  "'    r = Router()'," ^
  "'    r.include_router(commands_router(sessionmaker, settings))'," ^
  "'    return r'" ^
  "); Set-Content -Encoding UTF8 -Path 'app\bot\dispatcher.py' -Value $lines"

REM =========================
REM app/bot/keyboards.py
REM =========================
powershell -NoProfile -Command ^
  "$lines=@(" ^
  "'from aiogram.types import InlineKeyboardMarkup'," ^
  "'from aiogram.utils.keyboard import InlineKeyboardBuilder'," ^
  "''" ^
  "'def inbox_keyboard(draft_id: int, source_url: str) -> InlineKeyboardMarkup:'," ^
  "'    kb = InlineKeyboardBuilder()'," ^
  "'    kb.button(text=\"❌ Отбросить\", callback_data=f\"inbox_reject:{draft_id}\")'," ^
  "'    kb.button(text=\"🔗 Источник\", url=source_url)'," ^
  "'    kb.adjust(2)'," ^
  "'    return kb.as_markup()'" ^
  "); Set-Content -Encoding UTF8 -Path 'app\bot\keyboards.py' -Value $lines"

REM =========================
REM app/bot/commands.py
REM =========================
powershell -NoProfile -Command ^
  "$lines=@(" ^
  "'from aiogram import Router, F'," ^
  "'from aiogram.filters import Command'," ^
  "'from aiogram.types import Message, CallbackQuery'," ^
  "''" ^
  "'from app.storage import repo'," ^
  "'from app.pipeline.fetcher import fetch_rss_items'," ^
  "'from app.pipeline.extractor import download_html, extract_text, pick_image_url'," ^
  "'from app.pipeline.filtering import domain_allowed, passes_min_len'," ^
  "'from app.utils.url import normalize_url'," ^
  "'from app.utils.text import sha256_hex, clip_text'," ^
  "'from app.storage.models import Draft'," ^
  "'from app.bot.keyboards import inbox_keyboard'," ^
  "''" ^
  "'def router(sessionmaker, settings):'," ^
  "'    r = Router()'," ^
  "''" ^
  "'    @r.message(Command(\"start\"))'," ^
  "'    async def start(m: Message):'," ^
  "'        await m.answer('," ^
  "'            \"Stage-1 bot ready.\\n\"'," ^
  "'            \"1) /set_group in your working group\\n\"'," ^
  "'            \"2) /set_inbox_topic inside INBOX topic\\n\"'," ^
  "'            \"3) /add_source <rss_url>\\n\"'," ^
  "'            \"4) /fetch\"'," ^
  "'        )'," ^
  "''" ^
  "'    @r.message(Command(\"set_group\"))'," ^
  "'    async def set_group(m: Message):'," ^
  "'        st = await repo.update_settings(sessionmaker, group_chat_id=m.chat.id)'," ^
  "'        await m.answer(f\"✅ group_chat_id saved: <code>{st.group_chat_id}</code>\")'," ^
  "''" ^
  "'    @r.message(Command(\"set_inbox_topic\"))'," ^
  "'    async def set_inbox_topic(m: Message):'," ^
  "'        topic_id = getattr(m, \"message_thread_id\", None)'," ^
  "'        if not topic_id:'," ^
  "'            await m.answer(\"⚠️ Send this inside a Topic (Forum thread).\")'," ^
  "'            return'," ^
  "'        st = await repo.update_settings(sessionmaker, inbox_topic_id=topic_id)'," ^
  "'        await m.answer(f\"✅ inbox_topic_id saved: <code>{st.inbox_topic_id}</code>\")'," ^
  "''" ^
  "'    @r.message(Command(\"add_source\"))'," ^
  "'    async def add_source(m: Message):'," ^
  "'        parts = (m.text or \"\").split(maxsplit=1)'," ^
  "'        if len(parts) != 2:'," ^
  "'            await m.answer(\"Usage: /add_source <rss_url>\")'," ^
  "'            return'," ^
  "'        src = await repo.add_source(sessionmaker, parts[1])'," ^
  "'        await m.answer(f\"✅ Added source #{src.id}: {src.url}\")'," ^
  "''" ^
  "'    @r.message(Command(\"list_sources\"))'," ^
  "'    async def list_sources(m: Message):'," ^
  "'        sources = await repo.list_sources(sessionmaker)'," ^
  "'        if not sources:'," ^
  "'            await m.answer(\"No sources yet. Use /add_source <rss_url>\")'," ^
  "'            return'," ^
  "'        await m.answer(\"\\n\".join([f\"#{s.id} | enabled={s.enabled} | {s.url}\" for s in sources]))'," ^
  "''" ^
  "'    @r.message(Command(\"fetch\"))'," ^
  "'    async def fetch(m: Message):'," ^
  "'        st = await repo.get_or_create_settings(sessionmaker)'," ^
  "'        if not st.group_chat_id or not st.inbox_topic_id:'," ^
  "'            await m.answer(\"⚠️ Configure first: /set_group and /set_inbox_topic\")'," ^
  "'            return'," ^
  "'        sources = [s for s in (await repo.list_sources(sessionmaker)) if s.enabled]'," ^
  "'        if not sources:'," ^
  "'            await m.answer(\"⚠️ No enabled sources. Add with /add_source <rss_url>\")'," ^
  "'            return'," ^
  "'        produced = 0'," ^
  "'        for src in sources:'," ^
  "'            items = fetch_rss_items(src.url, limit=st.fetch_limit)'," ^
  "'            for it in items:'," ^
  "'                nurl = normalize_url(it.url)'," ^
  "'                h = sha256_hex(nurl)'," ^
  "'                if not domain_allowed(nurl, settings.whitelist_domains, settings.blacklist_domains):'," ^
  "'                    continue'," ^
  "'                if await repo.draft_exists_by_hash(sessionmaker, h):'," ^
  "'                    continue'," ^
  "'                try:'," ^
  "'                    html = await download_html(nurl)'," ^
  "'                    text = extract_text(html)'," ^
  "'                except Exception:'," ^
  "'                    continue'," ^
  "'                if not passes_min_len(text, settings.min_text_len):'," ^
  "'                    continue'," ^
  "'                img_url = pick_image_url(html)'," ^
  "'                excerpt = clip_text(text, 650)'," ^
  "'                title = (it.title or \"Untitled\").strip()'," ^
  "'                post = f\"<b>{title}</b>\\n{excerpt}\\n\\n#AI #Science\"'," ^
  "'                d = Draft('," ^
  "'                    source_url=it.url, normalized_url=nurl, normalized_url_hash=h,'," ^
  "'                    title_en=it.title, excerpt_en=excerpt, post_text=post,'," ^
  "'                    source_image_url=img_url, has_image=bool(img_url), state=\"INBOX\"'," ^
  "'                )'," ^
  "'                d = await repo.insert_draft(sessionmaker, d)'," ^
  "'                msg = await m.bot.send_message('," ^
  "'                    chat_id=st.group_chat_id,'," ^
  "'                    message_thread_id=st.inbox_topic_id,'," ^
  "'                    text=post,'," ^
  "'                    reply_markup=inbox_keyboard(d.id, nurl),'," ^
  "'                    disable_web_page_preview=False'," ^
  "'                )'," ^
  "'                await repo.attach_card(sessionmaker, d.id, st.group_chat_id, st.inbox_topic_id, msg.message_id)'," ^
  "'                produced += 1'," ^
  "'                if produced >= st.fetch_limit: break'," ^
  "'            if produced >= st.fetch_limit: break'," ^
  "'        await m.answer(f\"✅ Done. Sent to INBOX: {produced}\")'," ^
  "''" ^
  "'    @r.callback_query(F.data.startswith(\"inbox_reject:\"))'," ^
  "'    async def inbox_reject(cb: CallbackQuery):'," ^
  "'        try:'," ^
  "'            draft_id = int(cb.data.split(\":\", 1)[1])'," ^
  "'        except Exception:'," ^
  "'            await cb.answer(\"Bad id\")'," ^
  "'            return'," ^
  "'        await repo.set_draft_state(sessionmaker, draft_id, \"REJECTED\")'," ^
  "'        # delete the card message'," ^
  "'        try:'," ^
  "'            await cb.message.delete()'," ^
  "'        except Exception:'," ^
  "'            pass'," ^
  "'        await cb.answer(\"Rejected\")'," ^
  "''" ^
  "'    return r'" ^
  "); Set-Content -Encoding UTF8 -Path 'app\bot\commands.py' -Value $lines"

echo.
echo ✅ Project created: %cd%
echo Next steps:
echo 1) copy .env.example .env
echo 2) set BOT_TOKEN in .env
echo 3) docker compose up --build
echo.
pause
