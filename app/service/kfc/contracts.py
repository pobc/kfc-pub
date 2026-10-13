HOME_PATH = "/preorder-taro/home"
STORE_SEARCH_API = "/preorder-portal/api/v2/store/searchByCityCodeAndKeyword"
INIT_PREORDER_API = "/preorder-portal/api/v2/init/combine/preorder"
CART_CREATE_API = "/preorder-portal/api/v2/cart/create"
CART_UPDATE_API = "/preorder-portal/api/v2/cart/update"
CART_USE_COUPON_CODE_API = "/preorder-portal/api/v2/cart/useCouponCode"
CART_CONFIRM_API = "/preorder-portal/api/v2/cart/confirm"
ORDER_SUBMIT_API = "/preorder-portal/api/v2/order/submit"
ORDER_STATUS_API = "/preorder-portal/api/v2/order/getOrderStatusById"

USE_COUPON_CLIENT_VERSION = "v6.820(f472c683)"
USE_COUPON_FVERSION = "260629"

CART_CREATE_ENCODE_LIST = [
    "address.mainAddress",
    "address.address",
    "address.phone",
    "address.supplementalAddress",
    "address.name",
    "store.phone",
    "store.subStores.phone",
    "frontStore.address.address",
    "frontStore.address.mainAddress",
    "frontStore.address.mainaddress",
    "frontStore.address.name",
    "frontStore.address.phone",
    "frontStore.originStore.phone",
    "frontStore.store.phone",
]
