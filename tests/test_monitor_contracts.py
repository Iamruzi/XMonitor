from __future__ import annotations

from twitter_monitor.contracts import extract_token_contracts


def test_extracts_solana_contract_and_gmgn_link() -> None:
    contracts = extract_token_contracts(
        "CA: EmcxFTNVDqyLHp11NvwvLZ4D7LKGbG9i7B8RF7dwpump"
    )

    assert len(contracts) == 1
    contract = contracts[0].to_dict()
    assert contract["chain"] == "sol"
    assert contract["chainLabel"] == "Solana"
    assert contract["address"] == "EmcxFTNVDqyLHp11NvwvLZ4D7LKGbG9i7B8RF7dwpump"
    assert contract["links"][0]["url"] == (
        "https://gmgn.ai/sol/token/EmcxFTNVDqyLHp11NvwvLZ4D7LKGbG9i7B8RF7dwpump"
    )


def test_extracts_evm_contract_with_chain_context() -> None:
    contracts = extract_token_contracts(
        "Base CA: 0x1234567890abcdef1234567890abcdef12345678"
    )

    assert len(contracts) == 1
    contract = contracts[0].to_dict()
    assert contract["chain"] == "base"
    assert contract["links"] == [
        {
            "label": "GMGN Base K线",
            "url": "https://gmgn.ai/base/token/0x1234567890abcdef1234567890abcdef12345678",
        }
    ]


def test_unknown_evm_contract_gets_common_gmgn_links() -> None:
    contracts = extract_token_contracts(
        "CA: 0x1234567890abcdef1234567890abcdef12345678"
    )

    links = contracts[0].to_dict()["links"]
    assert [link["label"] for link in links] == [
        "GMGN Ethereum K线",
        "GMGN BSC K线",
        "GMGN Base K线",
        "GMGN X Layer K线",
    ]


def test_xlayer_contract_gets_gmgn_link() -> None:
    contracts = extract_token_contracts(
        "X Layer CA: 0x1234567890abcdef1234567890abcdef12345678"
    )

    contract = contracts[0].to_dict()
    assert contract["chain"] == "xlayer"
    assert contract["links"] == [
        {
            "label": "GMGN X Layer K线",
            "url": "https://gmgn.ai/xlayer/token/0x1234567890abcdef1234567890abcdef12345678",
        }
    ]
