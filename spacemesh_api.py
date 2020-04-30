import aiohttp
import asyncio
import yaml
import json
import time
import datetime
import xdrlib
from pure25519.eddsa import H, Hint
from pure25519.basic import (bytes_to_clamped_scalar, bytes_to_element, bytes_to_scalar, scalar_to_bytes, Base, L)
from pure25519.eddsa import create_signing_key, create_verifying_key


with open("config.yaml", 'r') as config:
    cfg = yaml.load(config, Loader=yaml.FullLoader)

rpc_url = f'http://{cfg["rpc"]["ip"]}:{cfg["rpc"]["port"]}/v1/'
confirm_timeout_min = cfg["faucet"]["confirmation_timeout_min"]
confirm_check_period_sec = cfg["faucet"]["check_period_sec"]


class TxGenerator:
    def __init__(self, pub, pri):
        self.publicK = bytes.fromhex(pub)
        self.privateK = bytes.fromhex(pri)

    def generate(self, dst, nonce, gas_limit, fee, amount):
        ADDRESS_SIZE = 20
        SIGNATURE_SIZE = 64
        p = xdrlib.Packer()
        p.pack_hyper(nonce)
        dst_bytes = bytes.fromhex(dst)
        # get the LAST 20 bytes of the dst address
        addr = dst_bytes[-ADDRESS_SIZE:]
        p.pack_fstring(ADDRESS_SIZE, addr)
        p.pack_hyper(gas_limit)
        p.pack_hyper(fee)
        p.pack_hyper(amount)

        data = p.get_buffer()
        sign = signature2(data, self.privateK)
        p.pack_fstring(SIGNATURE_SIZE, sign)
        return p.get_buffer()


def signature2(m, sk):
    assert len(sk) == 32
    h = H(sk[:32])
    a_bytes, inter = h[:32], h[32:]
    a = bytes_to_clamped_scalar(a_bytes)
    r = Hint(inter + m)
    R = Base.scalarmult(r)
    R_bytes = R.to_bytes()
    S = r + Hint(R_bytes + m) * a
    return R_bytes + scalar_to_bytes(S)


async def send_transaction(session, frm, to, private_key, amount=0, gas_price=10, gas_limit=20, nonce=None):
    try:
        gas_limit = gas_price + 1 if not gas_limit else gas_limit
        if len(private_key) == 102:
            private_key = private_key[:64]

        tx_gen = TxGenerator(pub=frm.replace("0x", ""), pri=private_key)
        nonce = int(await get_nonce(session, frm))
        if amount < int(await get_balance(session, frm)) - gas_limit:
            Exception(f'Insufficient funds: {amount}')

        tx_bytes = tx_gen.generate(to.replace("0x", ""), nonce, gas_limit, gas_price, amount)
        tx_field = '{"tx":TX_BYTES}'.replace("TX_BYTES", str(list(tx_bytes)))
        tx_result = await post_send(url=rpc_url + "submittransaction", data=tx_field, session=session)
        return tx_result

    except Exception as sendErr:
        print(sendErr)


async def post_send(session, url, data):
    headers = {"Content-Type": "application/json"}
    try:
        async with session.post(url=url, data=data, headers=headers) as resp:
            data = await resp.text()
            if type(data) is None or "error" in data:
                return await resp.text()
            else:
                return await resp.json()

    except Exception as err:
        print(await resp.text())
        print(f'{url}\nError: {err}\n')


async def get_nonce(session, addr: str):
    d = await post_send(session, rpc_url+"nonce", '{"address": "ADDR"}'.replace("ADDR", addr))
    try:
        return d["value"]

    except Exception:
        return f"ERROR: Uninitialized address {addr}"


async def get_balance(session, addr: str):
    d = await post_send(session, rpc_url+"balance", '{"address": "ADDR"}'.replace("ADDR", addr))
    try:
        return d["value"]

    except Exception:
        return f"ERROR: Uninitialized address {addr}"


async def get_node_status(session):
    return await post_send(session, rpc_url+"nodestatus", '')


async def tx_subscription(session, tx_id: str):
    transaction_coldown = confirm_timeout_min * 60
    trans_time = time.time()

    while True:
        resp = await get_transaction_info(session, trans_id_hex=tx_id)
        if time.time() > trans_time + transaction_coldown:
            print(f"Transaction was not confirmed within {confirm_timeout_min} minutes")
            return "timeout"

        elif "transaction not found" in str(resp):
            print(f"Transaction was removed from mem pool {tx_id}")
            return "removed"

        elif "status" in str(resp):
            if resp["status"] == "CONFIRMED":
                confirm_time = str(datetime.timedelta(seconds=time.time() - trans_time))
                print(f"Transaction confirmation took {confirm_time}")
                return confirm_time
        await asyncio.sleep(confirm_check_period_sec)


async def get_transactions_ids(session, addr: str):
    d = await post_send(session, rpc_url+"accounttxs", '{ "account": { "address": "ADDR"} }'.replace("ADDR", addr))
    print(d)
    return d


async def get_transaction_info(session, trans_id_hex: str):
    tx_id_bytes_array = list(bytearray.fromhex(trans_id_hex.replace("0x", "")))
    data = '{"id":TX_ID}'.replace("TX_ID", str(tx_id_bytes_array))
    resp = await post_send(session, data=data, url=rpc_url + "gettransaction")
    return resp


def addr_from_pub(pub: str) -> str:
    return pub[24:]


async def dump_all_transactions(session, address: str, dump_to_file: bool = True):
    balance = await get_balance(session, address)
    transactions_ids = await get_transactions_ids(session, address)
    unique_transactions_hashes = list(set(transactions_ids["txs"]))
    transactions_len = len(unique_transactions_hashes)
    all_transactions_json = {"address": address,
                             "balance": balance,
                             "total_transactions": transactions_len,
                             "in_transactions":    0,
                             "out_transactions":   0,
                             "transactions:":      []}

    for i, t in enumerate(unique_transactions_hashes):
        trans_info = await get_transaction_info(session, t)
        print(i)
        all_transactions_json["transactions:"].append(trans_info)
        try:
            if "amount" not in trans_info:
                trans_info.update({"amount": "0"})

            if trans_info["sender"]["address"] == address.replace("0x", ""):
                all_transactions_json["transactions:"][i].update({"transaction_type": "OUT"})
                all_transactions_json["out_transactions"] += 1

            else:
                all_transactions_json["transactions:"][i].update({"transaction_type": "IN"})
                all_transactions_json["in_transactions"] += 1

        except Exception as err:
            print(err)

    with open(f"{address[:15]}.json", "w") as f:
        json.dump(all_transactions_json, f, indent=2, sort_keys=False)
    await session.close()


