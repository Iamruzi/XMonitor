"""Token contract extraction and chart-link helpers for monitor events."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_EVM_ADDRESS_RE = re.compile(r"(?<![A-Za-z0-9])0[xX][a-fA-F0-9]{40}(?![A-Za-z0-9])")
_SOLANA_ADDRESS_RE = re.compile(
    r"(?<![%s])([%s]{32,44})(?![%s])"
    % (_BASE58_ALPHABET, _BASE58_ALPHABET, _BASE58_ALPHABET)
)
_SOLANA_HINTS = (
    "ca",
    "contract",
    "合约",
    "代币",
    "mint",
    "sol",
    "solana",
    "pump",
    "pump.fun",
    "raydium",
    "bonk",
)
_CHAIN_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("xlayer", ("x layer", "xlayer", "okb chain", "okx layer")),
    ("base", ("base", "coinbase base")),
    ("bsc", ("bsc", "bnb", "bnb chain", "binance smart chain", "bep20", "fourmeme")),
    ("eth", ("eth", "ethereum", "erc20", "erc-20", "uniswap")),
    ("arb", ("arbitrum", "arb")),
    ("op", ("optimism", "op mainnet")),
    ("polygon", ("polygon", "matic")),
    ("avax", ("avalanche", "avax")),
    ("blast", ("blast",)),
    ("linea", ("linea",)),
    ("scroll", ("scroll",)),
    ("zksync", ("zksync", "zk sync")),
)
_CHAIN_LABELS = {
    "sol": "Solana",
    "eth": "Ethereum",
    "bsc": "BSC",
    "base": "Base",
    "xlayer": "X Layer",
    "arb": "Arbitrum",
    "op": "Optimism",
    "polygon": "Polygon",
    "avax": "Avalanche",
    "blast": "Blast",
    "linea": "Linea",
    "scroll": "Scroll",
    "zksync": "zkSync",
    "evm": "EVM",
}
_GMGN_SUPPORTED_CHAINS = {"sol", "eth", "bsc", "base", "xlayer"}


@dataclass(frozen=True)
class ChartLink:
    label: str
    url: str

    def to_dict(self) -> dict[str, str]:
        return {"label": self.label, "url": self.url}


@dataclass(frozen=True)
class TokenContract:
    address: str
    chain: str
    chain_label: str
    kind: str
    links: tuple[ChartLink, ...]

    def to_dict(self) -> dict[str, Any]:
        data = {
            "address": self.address,
            "chain": self.chain,
            "chainLabel": self.chain_label,
            "kind": self.kind,
            "links": [link.to_dict() for link in self.links],
        }  # type: dict[str, Any]
        gmgn_url = next((link.url for link in self.links if link.url.startswith("https://gmgn.ai/")), "")
        if gmgn_url:
            data["gmgnUrl"] = gmgn_url
        return data


def extract_token_contracts(*values: str) -> list[TokenContract]:
    """Extract likely Solana and EVM token contracts from free-form event text."""

    text = "\n".join(str(value or "") for value in values if value)
    if not text.strip():
        return []

    contracts: list[TokenContract] = []
    seen: set[str] = set()
    evm_spans: list[tuple[int, int]] = []
    for match in _EVM_ADDRESS_RE.finditer(text):
        address = match.group(0)
        key = address.lower()
        if key in seen:
            continue
        chain = _detect_evm_chain(text, match.start(), match.end())
        contracts.append(
            TokenContract(
                address=address,
                chain=chain,
                chain_label=_CHAIN_LABELS.get(chain, chain.upper()),
                kind="evm",
                links=tuple(_chart_links(chain, address)),
            )
        )
        seen.add(key)
        evm_spans.append(match.span())

    for match in _SOLANA_ADDRESS_RE.finditer(text):
        address = match.group(1)
        if any(_spans_overlap(match.span(1), span) for span in evm_spans):
            continue
        if address in seen or not _looks_like_solana_contract(text, match.start(1), match.end(1)):
            continue
        contracts.append(
            TokenContract(
                address=address,
                chain="sol",
                chain_label=_CHAIN_LABELS["sol"],
                kind="solana",
                links=tuple(_chart_links("sol", address)),
            )
        )
        seen.add(address)

    return contracts


def enrich_payload_with_contracts(
    payload: dict[str, Any] | None,
    *values: str,
) -> dict[str, Any]:
    """Return a copy of payload with tokenContracts added when CA text is found."""

    enriched = dict(payload or {})
    if isinstance(enriched.get("tokenContracts"), list):
        return enriched
    contracts = extract_token_contracts(*values)
    if contracts:
        enriched["tokenContracts"] = [contract.to_dict() for contract in contracts]
    return enriched


def event_token_contracts(event: dict[str, Any]) -> list[dict[str, Any]]:
    payload = _event_payload(event)
    raw_contracts = payload.get("tokenContracts")
    if not isinstance(raw_contracts, list):
        return []
    contracts = []
    for item in raw_contracts:
        normalized = normalize_token_contract(item)
        if normalized:
            contracts.append(normalized)
    return contracts


def event_has_token_contracts(event: dict[str, Any]) -> bool:
    return bool(event_token_contracts(event))


def normalize_token_contract(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    address = str(value.get("address") or "").strip()
    if not address:
        return None
    chain = str(value.get("chain") or "").strip().lower()
    if not chain:
        chain = "sol" if _SOLANA_ADDRESS_RE.fullmatch(address) else "evm"
    kind = str(value.get("kind") or ("solana" if chain == "sol" else "evm"))
    chain_label = str(value.get("chainLabel") or _CHAIN_LABELS.get(chain, chain.upper()))
    raw_links = value.get("links")
    links = []
    if isinstance(raw_links, list):
        for raw_link in raw_links:
            if not isinstance(raw_link, dict):
                continue
            label = str(raw_link.get("label") or "").strip()
            url = str(raw_link.get("url") or "").strip()
            if label and url:
                links.append({"label": label, "url": url})
    if not links:
        links = [link.to_dict() for link in _chart_links(chain, address)]
    normalized = {
        "address": address,
        "chain": chain,
        "chainLabel": chain_label,
        "kind": kind,
        "links": links,
    }  # type: dict[str, Any]
    gmgn_url = str(value.get("gmgnUrl") or "").strip()
    if gmgn_url:
        normalized["gmgnUrl"] = gmgn_url
    return normalized


def gmgn_token_url(chain: str, address: str) -> str:
    return "https://gmgn.ai/%s/token/%s" % (chain, address)


def _event_payload(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("payload_json") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _chart_links(chain: str, address: str) -> list[ChartLink]:
    if chain in _GMGN_SUPPORTED_CHAINS:
        return [ChartLink("GMGN %s K线" % _CHAIN_LABELS[chain], gmgn_token_url(chain, address))]
    if chain == "evm":
        return [
            ChartLink("GMGN Ethereum K线", gmgn_token_url("eth", address)),
            ChartLink("GMGN BSC K线", gmgn_token_url("bsc", address)),
            ChartLink("GMGN Base K线", gmgn_token_url("base", address)),
            ChartLink("GMGN X Layer K线", gmgn_token_url("xlayer", address)),
        ]
    return [ChartLink("DexScreener K线", "https://dexscreener.com/search?q=%s" % address)]


def _looks_like_solana_contract(text: str, start: int, end: int) -> bool:
    address = text[start:end]
    if address.startswith("0x"):
        return False
    if address.lower().endswith("pump"):
        return True
    window = text[max(0, start - 48) : min(len(text), end + 48)].lower()
    return any(hint in window for hint in _SOLANA_HINTS)


def _detect_evm_chain(text: str, start: int, end: int) -> str:
    window = text[max(0, start - 96) : min(len(text), end + 96)].lower()
    full_text = text.lower()
    for chain, hints in _CHAIN_HINTS:
        if any(_term_in_text(window, hint) for hint in hints):
            return chain
    for chain, hints in _CHAIN_HINTS[:4]:
        if any(_term_in_text(full_text, hint) for hint in hints):
            return chain
    return "evm"


def _term_in_text(text: str, term: str) -> bool:
    escaped = re.escape(term)
    if re.fullmatch(r"[a-z0-9][a-z0-9 ._-]*", term):
        return re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % escaped, text) is not None
    return term in text


def _spans_overlap(first: tuple[int, int], second: tuple[int, int]) -> bool:
    return first[0] < second[1] and second[0] < first[1]
