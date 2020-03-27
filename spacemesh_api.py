import requests
import yaml
import json
import time
import xdrlib
from pure25519.eddsa import H, Hint
from pure25519.basic import (bytes_to_clamped_scalar, bytes_to_element, bytes_to_scalar, scalar_to_bytes, Base, L)
from pure25519.eddsa import create_signing_key, create_verifying_key


with open("config.yaml", 'r') as config:
    cfg = yaml.load(config, Loader=yaml.FullLoader)

rpc_url = f'http://{cfg["rpc"]["ip"]}:{cfg["rpc"]["port"]}/v1/'


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


def send_transaction(frm, to, private_key, amount=0, gas_price=10, gas_limit=20, nonce=None):
    gas_limit = gas_price + 1 if not gas_limit else gas_limit
    if len(private_key) == 102: private_key = private_key[:64]

    tx_gen = TxGenerator(pub=frm.replace("0x", ""), pri=private_key)
    nonce = int(get_nonce(frm))
    if amount < int(get_balance(frm)) - gas_limit:
        Exception(f'Insufficient funds: {amount}')

    tx_bytes = tx_gen.generate(to.replace("0x", ""), nonce, gas_limit, gas_price, amount)
    tx_field = '{"tx":TX_BYTES}'.replace("TX_BYTES", str(list(tx_bytes)))
    tx_result = post_send(rpc_url + "submittransaction", tx_field)
    return tx_result


def post_send(url: str, data: str):
    headers = {"Content-Type": "application/json"}
    try:
        req = requests.post(url=url, data=data, headers=headers)
        if "submittransaction" in url:
            print(req.json())
        if req.status_code == 200 and req.json() is not None:
            return req.json()

    except Exception as err:
        print(f'{url}\n'
              f'Error: {err}\n'
              f'Data: {req.content}')


def get_nonce(addr: str):
    d = post_send(rpc_url+"nonce", '{"address": "ADDR"}'.replace("ADDR", addr))
    try:
        return d["value"]

    except Exception:
        print(f"Uninitialized address {addr}")


def get_balance(addr: str):
    d = post_send(rpc_url+"balance", '{"address": "ADDR"}'.replace("ADDR", addr))
    try:
        return d["value"]

    except Exception:
        print(f"Uninitialized address {addr}")


def get_node_status():
    return post_send(rpc_url+"nodestatus", '')


def tx_subscription(tx_id: str):
    # Not implemented yet
    pass


def get_transactions_ids(addr: str):
    d = post_send(rpc_url+"accounttxs", '{ "account": { "address": "ADDR"} }'.replace("ADDR", addr))
    print(d)
    return d


def get_transaction_info(trans_id_hex: str):
    tx_id_bytes_array = list(bytearray.fromhex(trans_id_hex.replace("0x", "")))
    d = '{"id":TX_ID}'.replace("TX_ID", str(tx_id_bytes_array))
    print(d)
    resp = post_send(rpc_url + "gettransaction", d)
    return resp


def addr_from_pub(pub: str) -> str:
    return pub[24:]


def dump_all_transactions(address: str, dump_to_file: bool = True):
    balance = get_balance(address)
    transactions_ids = get_transactions_ids(address)
    unique_transactions_hashes = list(set(transactions_ids["txs"]))
    print(unique_transactions_hashes)
    transactions_len = len(unique_transactions_hashes)
    all_transactions_json = {"address": address,
                             "balance": balance,
                             "total_transactions": transactions_len,
                             "in_transactions":    0,
                             "out_transactions":   0,
                             "transactions:":      []}

    for i, t in enumerate(unique_transactions_hashes):
        trans_info = get_transaction_info(t)
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

    with open(f"{address}_transactions.json", "w") as f:
        json.dump(all_transactions_json, f, indent=2, sort_keys=False)
    return all_transactions_json



