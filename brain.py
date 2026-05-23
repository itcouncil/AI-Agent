from __future__ import annotations

import json
import re
import urllib.parse
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from config import Settings
from storage import Store


STOP_WORDS = {
    "about", "after", "again", "also", "because", "before", "could", "from",
    "have", "hello", "here", "just", "know", "like", "please", "that", "their",
    "there", "this", "what", "when", "where", "which", "with", "would", "your",
}


@dataclass
class Answer:
    text: str
    should_send: bool = True


class AgentBrain:
    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store

    def reply(self, phone: str, message: str) -> Answer:
        normalized = message.strip().lower()
        if normalized in {"stop", "unsubscribe", "cancel"}:
            self.store.opt_out(phone)
            return Answer("You have been opted out. You will not receive automated replies here.")
        if normalized in {"forget me", "delete my data", "erase me"}:
            self.store.forget(phone)
            return Answer("I deleted the conversation memory stored for this number.")
        if normalized in {"human", "agent", "representative", "talk to human"}:
            return Answer(self._handoff_text())
        if self.store.is_opted_out(phone):
            return Answer("", should_send=False)

        terms = keywords(message)
        catalog_hits = search_catalog(self.settings.catalog_path, terms)
        memory_hits = self.store.search_memories(phone, terms)
        web_hits = self._web_search(message) if wants_current_info(message) else []
        recent = self.store.recent_messages(phone)

        answer = self._generate_answer(message, recent, catalog_hits, memory_hits, web_hits)
        self._learn(phone, message, answer)
        if self.settings.agent_disclosure and not answer.lower().startswith("automated assistant:"):
            answer = f"Automated assistant: {answer}"
        return Answer(answer[:3900])

    def _handoff_text(self) -> str:
        if self.settings.human_handoff_number:
            return f"I'll flag this for a human. You can also contact {self.settings.human_handoff_number}."
        return "I'll flag this for a human review. Please share the best time to reach you."

    def _generate_answer(
        self,
        message: str,
        recent: list,
        catalog_hits: list[str],
        memory_hits: list[str],
        web_hits: list[str],
    ) -> str:
        if self.settings.openai_api_key:
            ai_answer = self._openai_answer(message, recent, catalog_hits, memory_hits, web_hits)
            if ai_answer:
                return ai_answer

        context_parts = []
        if catalog_hits:
            context_parts.append("From the catalog: " + " ".join(catalog_hits[:3]))
        if memory_hits:
            context_parts.append("From our previous chat: " + " ".join(memory_hits[:3]))
        if web_hits:
            context_parts.append("From recent web results: " + " ".join(web_hits[:2]))
        if context_parts:
            return " ".join(context_parts) + " If you'd like, I can help with the next step."
        return (
            "I don't have enough verified information to answer that confidently yet. "
            "Please share a little more detail, or ask for a human if this is urgent."
        )

    def _openai_answer(
        self,
        message: str,
        recent: list,
        catalog_hits: list[str],
        memory_hits: list[str],
        web_hits: list[str],
    ) -> str:
        system = (
            "You are a transparent WhatsApp business assistant. Answer only from the "
            "provided catalog, memory, web snippets, and general knowledge. If unsure, say so. "
            "Do not pretend to be human. Be concise, factual, and helpful. For medical, legal, "
            "financial, safety, or identity-sensitive requests, recommend human review."
        )
        transcript = "\n".join(f"{row['direction']}: {row['body']}" for row in recent)
        prompt = {
            "conversation": transcript,
            "customer_message": message,
            "catalog_facts": catalog_hits,
            "conversation_memory": memory_hits,
            "web_results": web_hits,
        }
        payload = {
            "model": self.settings.openai_model,
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=True)},
            ],
            "max_output_tokens": 350,
        }
        request = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return ""
        return extract_openai_text(data)

    def _web_search(self, query: str) -> list[str]:
        if not self.settings.brave_search_api_key:
            return []
        url = "https://api.search.brave.com/res/v1/web/search?q=" + urllib.parse.quote(query)
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self.settings.brave_search_api_key,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return []
        results = data.get("web", {}).get("results", [])[:3]
        return [
            f"{item.get('title', '').strip()}: {item.get('description', '').strip()}"
            for item in results
            if item.get("description")
        ]

    def _learn(self, phone: str, user_message: str, assistant_answer: str) -> None:
        facts = extract_user_facts(user_message)
        for fact in facts:
            self.store.add_memory(phone, fact)
        if "my " in user_message.lower() and len(user_message) < 300:
            self.store.add_memory(phone, f"Customer said: {user_message}")
        self.store.add_message(phone, "assistant", assistant_answer)


def keywords(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z0-9]{3,}", text.lower())
    return [word for word in words if word not in STOP_WORDS]


def search_catalog(path: Path, terms: list[str], limit: int = 6) -> list[str]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n|(?<=\.)\s+", text) if chunk.strip()]
    scored: list[tuple[int, str]] = []
    for chunk in chunks:
        haystack = chunk.lower()
        score = sum(1 for term in terms if term in haystack)
        if score:
            scored.append((score, chunk))
    scored.sort(reverse=True, key=lambda item: item[0])
    return [chunk for _, chunk in scored[:limit]]


def wants_current_info(message: str) -> bool:
    return any(
        phrase in message.lower()
        for phrase in ("latest", "today", "current", "news", "now", "internet", "online", "price of")
    )


def extract_user_facts(message: str) -> list[str]:
    patterns = [
        r"\bmy ([a-zA-Z ]{2,40}) is ([^.!?\n]{2,120})",
        r"\bi prefer ([^.!?\n]{2,120})",
        r"\bi am interested in ([^.!?\n]{2,120})",
    ]
    facts: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, message, flags=re.IGNORECASE):
            facts.append(" ".join(match.group(0).split()))
    return facts


def extract_openai_text(data: dict) -> str:
    if data.get("output_text"):
        return str(data["output_text"]).strip()
    parts: list[str] = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                parts.append(content["text"])
    return "\n".join(parts).strip()
