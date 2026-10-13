from __future__ import annotations

import json
from pathlib import Path
import secrets
import shutil
import subprocess

import pytest
from pydantic import ValidationError

from app.api.schemas import ProductOptionGroupInput
from app.database import access_links, order_confirmation_logs, products
from app.errors import AppError
from app.service import access_link_service, checkout_service


@pytest.fixture(scope="module")
def meal():
    product = products.create_product(
        {
            "name": f"多选回归验证-{secrets.token_hex(6)}",
            "description": "多选回归验证",
            "parentLinkId": "__AUTO_MATCH_BY_NAME__",
        }
    )
    config = products.replace_product_options(
        product["id"],
        [
            {
                "name": "餐品",
                "selectCount": 2,
                "options": [
                    {"name": "香辣鸡腿堡"},
                    {"name": "香辣鸡翅"},
                    {"name": "黄金鸡块"},
                ],
            },
            {
                "name": "饮料及甜品",
                "selectCount": 1,
                "options": [
                    {"name": "百事可乐(中冰)"},
                    {"name": "葡式蛋挞"},
                ],
            },
        ],
    )
    link = access_links.create_access_links(product["id"], 1)[0]
    return product, config["groups"], link


def test_two_plus_one_selection_reaches_order_snapshot(meal) -> None:
    product, groups, link = meal
    ids = [option["id"] for option in groups[0]["options"][:2]] + [groups[1]["options"][0]["id"]]
    snapshot = products.selected_option_snapshot(product["id"], ids)
    assert [item["name"] for item in snapshot] == ["香辣鸡腿堡", "香辣鸡翅", "百事可乐(中冰)"]
    assert [group["selectCount"] for group in groups] == [2, 1]
    public = access_link_service.get_access_link_state(link["accessId"])
    assert public["products"][0]["optionGroups"] == groups
    order_confirmation_logs.record_order_confirmation(link["accessId"], "127.0.0.1", "回归验证", "回归验证")
    order_confirmation_logs.save_production_info(link["accessId"], "回归验证", snapshot)
    assert order_confirmation_logs.get_latest_production_info(link["accessId"]) == snapshot


@pytest.mark.parametrize("positions", [[], [0, 3], [0, 1], [0, 1, 2, 3], [0, 1, 3, 4], [0, 0, 3]])
def test_checkout_rejects_missing_excess_or_duplicate_selections(meal, positions) -> None:
    product, groups, link = meal
    options = [option for group in groups for option in group["options"]]
    with pytest.raises(AppError) as exc:
        checkout_service.apply_coupon(
            link["accessId"],
            store_code="回归验证",
            pack_type=2,
            items=[{"productId": product["id"], "quantity": 1}],
            option_ids=[options[index]["id"] for index in positions],
            visitor_ip="127.0.0.1",
        )
    assert exc.value.status_code == 400
    assert access_links.get_link(link["accessId"])["status"] == "UNUSED"


@pytest.mark.parametrize("count", [0, -1, 4, 1.5, True, "2"])
def test_invalid_group_count_preserves_saved_options(meal, count) -> None:
    product, groups, _ = meal
    with pytest.raises(AppError):
        products.replace_product_options(product["id"], [{**groups[0], "selectCount": count}])
    assert products.list_product_options(product["id"])["groups"] == groups


@pytest.mark.parametrize("count", [0, -1, 1.5, True, "2"])
def test_api_rejects_invalid_selection_count(meal, count) -> None:
    with pytest.raises(ValidationError):
        ProductOptionGroupInput.model_validate({**meal[1][0], "selectCount": count})


def test_legacy_group_defaults_to_single_select_and_rejects_foreign_option(meal) -> None:
    product = products.create_product(
        {
            "name": f"单选兼容验证-{secrets.token_hex(6)}",
            "parentLinkId": "__AUTO_MATCH_BY_NAME__",
        }
    )
    assert products.selected_option_snapshot(product["id"], []) == []
    config = products.replace_product_options(
        product["id"],
        [
            {
                "name": "主食",
                "options": [{"name": "香辣鸡腿堡"}, {"name": "老北京鸡肉卷"}],
            }
        ],
    )
    group = config["groups"][0]
    assert group["selectCount"] == 1
    assert ProductOptionGroupInput.model_validate({"name": group["name"], "options": group["options"]}).selectCount == 1
    ids = [option["id"] for option in group["options"]]
    assert len(products.selected_option_snapshot(product["id"], ids[:1])) == 1
    with pytest.raises(AppError, match="需选择1个"):
        products.selected_option_snapshot(product["id"], ids)
    with pytest.raises(AppError, match="不属于当前餐品"):
        products.selected_option_snapshot(meal[0]["id"], ids[:1])


def test_frontend_selection_with_persisted_option_groups(meal) -> None:
    root = Path(__file__).resolve().parents[2]
    node = shutil.which("node")
    assert node, "前端选择验证需要 Node.js"
    result = subprocess.run(
        [node, str(root / "tests/integration/option_selection_check.mjs")],
        input=json.dumps(meal[1]),
        text=True,
        encoding="utf-8",
        capture_output=True,
        cwd=root,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
