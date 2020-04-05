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
APPROVE_EMOJI = "💸"
REJECT_EMOJI = "🚫"
ACTIVE_REQUESTS = {}
decimal = 1e12
client = discord.Client()


@client.event
async def on_ready():
    logger.info(f'Logged in as {client.user}')


@client.event
async def on_message(message):
    message_timestamp = time.time()

    # Do not listen to your own messages
    if message.author == client.user:
        return

    if message.content.startswith('$help') and message.channel.name in LISTENING_CHANNELS:
        help_msg = f'**List of available commands:** \n' \
                   f'1. Request coins through the tap - send your address\n' \
                   f'**You can request coins no more than once every three hours* \n' \
                   f'Example:\n' \
                   f'`0xafed9a1c17ca7eaa7a6795dbc7bee1b1d992c7ba`\n\n' \
                   f'2. `$faucet_status` - displays the current status of the node where faucet is running\n\n' \
                   f'3. `$tx_info` - show transaction information for a specific transaction ID' \
                   f' (sender, receiver, fee, amount, status)\n' \
                   f'Example:\n' \
                   f'`$tx_info f3282db1dd705bf7893b8835efaa0649647c69c5a560250347bfd4a300af4912`'
        await message.channel.send(help_msg)

    # Show node synchronization settings
    if message.content.startswith('$faucet_status') and message.channel.name in LISTENING_CHANNELS:
        try:
            status = spacemesh_api.get_node_status()
            if "synced" in status:
                status = f'```' \
                         f'Peers:   {status["peers"]}\n' \
                         f'Synced:  {status["synced"]}\n' \
                         f'Layers:  {status["currentLayer"]}\\{status["syncedLayer"]}\n```'
            await message.channel.send(status)

        except Exception as statusErr:
            print(statusErr)

    if message.content.startswith('$tx_info') and message.channel.name in LISTENING_CHANNELS:
        try:
            hash_id = str(message.content).replace("$tx_info", "").replace(" ", "")
            if len(hash_id) == 64:
                tr_info = spacemesh_api.get_transaction_info(hash_id)
                # See this - https://github.com/spacemeshos/go-spacemesh/issues/1908
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
        requester = message.author
        requester_address = str(message.content)

        if requester.id in ACTIVE_REQUESTS:
            check_time = ACTIVE_REQUESTS[requester.id]["next_request"]
            if check_time > message_timestamp:
                please_wait_text = f'{requester.mention}, You can request coins no more than once every 3 hours.\n' \
                                   f'The next attempt is possible after {round((check_time - message_timestamp) / 60, 2)} minutes'
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

            faucet_balance = int(spacemesh_api.get_balance(ADDRESS))
            if faucet_balance > (FAUCET_AMOUNT + FAUCET_FEE):
                transaction = spacemesh_api.send_transaction(frm=ADDRESS, to=requester_address, amount=FAUCET_AMOUNT,
                                                             gas_price=FAUCET_FEE, private_key=PRIVATE_KEY)
                logger.info(f'Transaction result:\n{transaction}')
                if transaction["value"] == "ok":
                    await message.add_reaction(emoji=APPROVE_EMOJI)
                    await message.channel.send(f'{requester.mention}, Transaction has been sent. '
                                               f'Check TX status: `$tx_info {str(transaction["id"])}`')

            elif faucet_balance < (FAUCET_AMOUNT + FAUCET_FEE):
                logger.error(f'Insufficient funds: {faucet_balance}')
                await message.add_reaction(emoji=REJECT_EMOJI)
                await message.channel.send(f'@yaelh#5158,\n'
                                           f'Insufficient funds: {faucet_balance}. '
                                           f'It is necessary to replenish the faucet address: `0xafed9a1c17ca7eaa7a6795dbc7bee1b1d992c7ba`')

client.run(TOKEN)
