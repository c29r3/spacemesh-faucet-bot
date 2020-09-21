import asyncio
import aiofiles as aiof
import aiohttp
import discord
import spacemesh_api
import yaml
import logging
import time
import sys

# Turn Down Discord Logging
disc_log = logging.getLogger('discord')
disc_log.setLevel(logging.CRITICAL)

# Configure Logging
logging.basicConfig(stream=sys.stdout, level=logging.CRITICAL)
logger = logging.getLogger(__name__)

# Load config
with open("config.yaml", 'r') as config:
    cfg = yaml.load(config, Loader=yaml.FullLoader)

PUBLIC_KEY = str(cfg["public_key"])
ADDRESS = str(cfg["address"])
PRIVATE_KEY = str(cfg["private_key"])
TOKEN = str(cfg["faucet"]["discord_bot_token"])
LISTENING_CHANNELS = str(cfg["faucet"]["discord_listening_channel"])
FAUCET_AMOUNT = int(cfg["faucet"]["amount_to_send"])
FAUCET_FEE = int(cfg["faucet"]["fee"])
REQUEST_COLDOWN = int(cfg["faucet"]["request_coldown_sec"])
CONFIRM_TIMEOUT_MIN = int(cfg["faucet"]["confirmation_timeout_min"])
USER_ID_NOTIFY = int(cfg["faucet"]["user_id_notify"])
APPROVE_EMOJI = "💸"
REJECT_EMOJI = "🚫"
CONFIRMED_EMOJI = "✅"
ACTIVE_REQUESTS = {}
decimal = 1e12
client = discord.Client()

help_msg = f"""**List of available commands:** 
1. Request coins through the tap - send your address
*You can request coins no more than once every three hours* 

Transaction status explanation:
💸 - mean bot send transaction to your address, but the transaction has not yet been confirmed
✅ - transaction was successfully confirmed
🚫 - the transaction was not confirmed for some reason. You need to make another request
*Bot track transaction status only for 15 minutes*
*Average transaction confirmation time 10-13 minutes*

2. `$faucet_status` - displays the current status of the node where faucet is running

3. `$faucet_address` or `$tap_address` - show tap address

4. `$tx_info <TX_ID>` - show transaction information for a specific transaction ID
(sender, receiver, fee, amount, status)

5. `$balance <ADDRESS>` - show address balance

6. `$dump_txs <ADDRESS>` - get json file with all transactions
*Direct message to bot*"""


async def save_transaction_statistics(some_string: str):
    # with open("transactions.csv", "a") as csv_file:
    async with aiof.open("transactions.csv", "a") as csv_file:
        await csv_file.write(f'{some_string}\n')
        await csv_file.flush()


@client.event
async def on_ready():
    logger.info(f'Logged in as {client.user}')


@client.event
async def on_message(message):
    session = aiohttp.ClientSession()
    message_timestamp = time.time()
    requester = message.author
    usr1 = client.get_user(id=USER_ID_NOTIFY)

    # Do not listen to your own messages
    if message.author == client.user:
        return

    if message.content.startswith('$balance'):
        address = str(message.content).replace("$balance", "").replace(" ", "")
        if str(address[:2]) == "0x" and len(address) == 42:
            balance = await spacemesh_api.get_balance(session, address)
            if "error" in str(balance).lower():
                await message.channel.send(f'{message.author.mention} {str(balance)}')
            else:
                await message.channel.send(f'{message.author.mention}, {str(balance)} smidge ({int(balance) / decimal:.3f} SMH)')

    if message.content.startswith('$help'):
        await message.channel.send(help_msg)

    if message.content.startswith('$dump_txs'):
        address = str(message.content).replace("$dump_txs", "").replace(" ", "")
        if str(address[:2]) == "0x" and len(address) == 42:
            await spacemesh_api.dump_all_transactions(session, address)
            await requester.send(file=discord.File(f"{address[:15]}.json"))

    # Show node synchronization settings
    if message.content.startswith('$faucet_status'):
        print(requester.name, "status request")
        try:
            faucet_balance = await spacemesh_api.get_balance(session, ADDRESS)
            status = await spacemesh_api.get_node_status(session)
            if "synced" in status and "ERROR" not in str(faucet_balance):
                status = f'```' \
                         f'Balance: {int(faucet_balance) / decimal} SMH\n' \
                         f'Peers:   {status["peers"]}\n' \
                         f'Synced:  {status["synced"]}\n' \
                         f'Layers:  {status["currentLayer"]}\\{status["syncedLayer"]}\n```'
            await message.channel.send(status)

        except Exception as statusErr:
            print(statusErr)

    if message.content.startswith('$faucet_address') or  message.content.startswith('$tap_address') and message.channel.name in LISTENING_CHANNELS:
        try:
            await message.channel.send(f"Faucet address is: {ADDRESS}")
        except:
            print("Can't send message $faucet_address")

    if message.content.startswith('$tx_info') and message.channel.name in LISTENING_CHANNELS:
        try:
            hash_id = str(message.content).replace("$tx_info", "").replace(" ", "")
            if len(hash_id) == 64 or len(hash_id) == 66:
                tr_info = await spacemesh_api.get_transaction_info(session, hash_id)
                if "amount" and "fee" in str(tr_info):
                    tr_info = f'```' \
                              f'From:       0x{str(tr_info["sender"]["address"])}\n' \
                              f'To:         0x{str(tr_info["receiver"]["address"])}\n' \
                              f'Amount:     {float(int(tr_info["amount"]) / decimal)} SMH\n' \
                              f'Fee:        {int(tr_info["fee"])}\n' \
                              f'STATUS:     {tr_info["status"]}```'
                await message.channel.send(tr_info)
            else:
                await message.channel.send(f'Incorrect len hash id {hash_id}')

        except Exception as tx_infoErr:
            print(tx_infoErr)

    if str(message.content[:2]) == "0x" and len(message.content) == 42 and message.channel.name in LISTENING_CHANNELS:
        channel = message.channel
        requester_address = str(message.content)

        if requester.id in ACTIVE_REQUESTS:
            check_time = ACTIVE_REQUESTS[requester.id]["next_request"]
            if check_time > message_timestamp:
                please_wait_text = f'{requester.mention}, You can request coins no more than once every 3 hours.' \
                                   f'The next attempt is possible after ' \
                                   f'{round((check_time - message_timestamp) / 60, 2)} minutes'
                await channel.send(please_wait_text)
                return

            else:
                del ACTIVE_REQUESTS[requester.id]

        if requester.id not in ACTIVE_REQUESTS and requester_address not in ACTIVE_REQUESTS:
            ACTIVE_REQUESTS[requester.id] = {
                "address": requester_address,
                "requester": requester,
                "next_request": message_timestamp + REQUEST_COLDOWN}
            print(ACTIVE_REQUESTS)

            faucet_balance = int(await spacemesh_api.get_balance(session, ADDRESS))
            if faucet_balance > (FAUCET_AMOUNT + FAUCET_FEE):
                transaction = await spacemesh_api.send_transaction(session,
                                                                   frm=ADDRESS,
                                                                   to=requester_address,
                                                                   amount=FAUCET_AMOUNT,
                                                                   gas_price=FAUCET_FEE,
                                                                   private_key=PRIVATE_KEY)
                logger.info(f'Transaction result:\n{transaction}')
                if transaction["value"] == "ok":
                    await message.add_reaction(emoji=APPROVE_EMOJI)
                    confirm_time = await spacemesh_api.tx_subscription(session, transaction["id"])
                    if confirm_time == "removed":
                        await message.add_reaction(emoji=REJECT_EMOJI)
                        await message.channel.send(f'{requester.mention}, {transaction["id"]} was fail to send. '
                                                   f'You can do another request')
                        # remove the restriction on the request for coins, since the transaction was not completed
                        del ACTIVE_REQUESTS[requester.id]

                    elif confirm_time != "timeout":
                        await message.add_reaction(emoji=CONFIRMED_EMOJI)

                    elif confirm_time == "timeout":
                        await message.channel.send(f'{requester.mention}, Transaction confirmation took more than '
                                                   f'{CONFIRM_TIMEOUT_MIN} minutes. '
                                                   f'Check status manually: `$tx_info {str(transaction["id"])}`')
                        await usr1.send(f'Transaction confirmation took more than {CONFIRM_TIMEOUT_MIN} minutes: '
                                        f'{transaction["id"]}')

                    # await message.channel.send(f'TX_ID: {transaction["id"]} | Confirmation time: {confirm_time}')
                    await save_transaction_statistics(f'{transaction["id"]};{confirm_time}')
                    await session.close()

            elif faucet_balance < (FAUCET_AMOUNT + FAUCET_FEE):
                logger.error(f'Insufficient funds: {faucet_balance}')
                await message.add_reaction(emoji=REJECT_EMOJI)
                await message.channel.send(f'@yaelh#5158,\n'
                                           f'Insufficient funds: {faucet_balance}. '
                                           f'It is necessary to replenish the faucet address: `{ADDRESS}`')

client.run(TOKEN)
