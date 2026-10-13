from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class LoginInput(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class ProductCreateInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    imageUrl: str | None = None
    parentLinkId: str = Field(min_length=1, max_length=120)
    specLinkIds: list[str] = Field(default_factory=list)
    condimentLinkIds: list[str] = Field(default_factory=list)


class ProductUpdateInput(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    imageUrl: str | None = None
    parentLinkId: str | None = Field(default=None, min_length=1, max_length=120)


class ProductOptionInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    imageUrl: str | None = None


class ProductOptionGroupInput(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    selectCount: int = Field(default=1, ge=1, strict=True)
    options: list[ProductOptionInput] = Field(min_length=1)


class ProductOptionsInput(BaseModel):
    groups: list[ProductOptionGroupInput] = Field(default_factory=list)


class KfcProductNameMappingInput(BaseModel):
    kfcProductName: str = Field(min_length=1, max_length=500)
    productId: str = Field(min_length=1)


class AccessLinksCreateInput(BaseModel):
    productId: str = Field(min_length=1)
    count: int = Field(default=10, ge=1, le=500)


class AccessLinkUpdateInput(BaseModel):
    productId: str = Field(min_length=1)


class CouponUpdateInput(BaseModel):
    couponCode: str = Field(min_length=1, max_length=255)


class CouponBatchUpdateInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ManualCouponImportInput(BaseModel):
    productId: str = Field(min_length=1)
    batchName: str = Field(min_length=1, max_length=120)
    couponCodes: list[str] = Field(min_length=1)


class KfcSessionCreateInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    ticket: str = Field(min_length=1)
    userCode: str = Field(min_length=1, max_length=160)
    phone: str = Field(default="", max_length=40)
    openId: str = Field(default="", max_length=200)
    deviceId: str = Field(default="", max_length=200)
    cookie: str = ""
    remark: str = Field(default="", max_length=500)


class KfcSessionUpdateInput(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    ticket: str | None = Field(default=None, min_length=1)
    userCode: str | None = Field(default=None, min_length=1, max_length=160)
    phone: str | None = Field(default=None, max_length=40)
    openId: str | None = Field(default=None, max_length=200)
    deviceId: str | None = Field(default=None, max_length=200)
    cookie: str | None = None
    remark: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None


class KfcAuthStateImportInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    authState: dict[str, Any]
    remark: str = Field(default="", max_length=500)


class KfcCaptureSaveInput(BaseModel):
    name: str = Field(default="", max_length=120)
    remark: str = Field(default="", max_length=500)


class TaobaoLoginSaveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    captureId: str = Field(min_length=1, max_length=64)


class TaobaoKeywordInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    keyword: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2000)


class TaobaoPriceScrapeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    url: str = Field(min_length=1, max_length=2000)


class KfcCouponSyncInput(BaseModel):
    ticketId: str = Field(min_length=1)
    productId: str | None = None
    batchName: str | None = Field(default=None, max_length=120)


class SearchStoresInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gbCityCode: str = Field(min_length=1)
    keyword: str = ""


class ConfirmStoreInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    storeCode: str = Field(min_length=1, max_length=64)
    storeAddress: str = Field(min_length=1, max_length=1000)
    storeName: str = Field(min_length=1, max_length=200)
    cityName: str = Field(min_length=1, max_length=80)
    storeImageUrl: str = Field(min_length=1, max_length=2048, pattern=r"^https?://[^\s]+$")


class AssignCouponInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visitorIp: str = Field(min_length=1, max_length=64)


class OrderItemInput(BaseModel):
    productId: str
    quantity: int = Field(ge=1, le=10)


class SubmitOrderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    storeCode: str
    items: list[OrderItemInput]


class ApplyCouponInput(SubmitOrderInput):
    packType: Literal[1, 2]
    optionIds: list[int] = Field(default_factory=list, max_length=50)


class PublicVerificationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    challengeToken: str = Field(min_length=20, max_length=2048)
    answer: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
