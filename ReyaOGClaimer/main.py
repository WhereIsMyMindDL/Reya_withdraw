import time
import json
import random
import asyncio
import aiohttp
import questionary
import pandas as pd
from sys import stderr
from loguru import logger
from web3 import Web3
from web3.eth import AsyncEth
from eth_account.account import Account
from eth_account.messages import encode_typed_data

logger.remove()
logger.add(stderr,
           format="<lm>{time:HH:mm:ss}</lm> | <level>{level}</level> | <blue>{function}:{line}</blue> "
                  "| <lw>{message}</lw>")
abi = json.loads(
    '[{"inputs":[{"internalType":"address","name":"account","type":"address"}],"name":"balanceOf",'
    '"outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view",'
    '"type":"function"}]')


def async_error_handler(error_msg, retries=3):
    def decorator(func):
        async def wrapper(*args, **kwargs):
            for i in range(0, retries):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    logger.error(f"{error_msg}: {str(e)}")
                    if i == retries - 1:
                        return 0
                    await asyncio.sleep(2)

        return wrapper

    return decorator


class Worker:
    def __init__(self, private_key: str, proxy: str, cex_address: str, number_acc: int) -> None:
        self.private_key = private_key
        self.cex_address = cex_address
        self.scan: str = 'https://arbiscan.io/tx/'
        self.account = Account().from_key(private_key=private_key)
        self.w3 = Web3(
            provider=Web3.AsyncHTTPProvider(endpoint_uri='https://rpc.reya.network'),
            modules={"eth": AsyncEth},
            middlewares=[])
        self.w3_arb = Web3(
            provider=Web3.AsyncHTTPProvider(endpoint_uri='https://arb1.lava.build'),
            modules={"eth": AsyncEth},
            middlewares=[])
        self.proxy: str = f"http://{proxy}" if proxy is not None else None
        self.id: int = number_acc
        self.contract_erc20 = self.w3_arb.eth.contract(
            address=Web3.to_checksum_address('0xaf88d065e77c8cC2239327C5EDb3A432268e5831'), abi=abi)
        self.client = None
        self.withdraw_balance = None
        self.withdraw_balance_wei = None
        self.deadline = None

    async def create_message_for_withdraw(self) -> str or None:
        await Worker.get_balance(self)
        if self.withdraw_balance > 1:
            self.deadline: int = int(time.time() + 600000)
            message = {
                "types": {
                    "RemoveLiquidityBySig": [
                        {
                            "name": "verifyingChainId",
                            "type": "uint256"
                        },
                        {
                            "name": "caller",
                            "type": "address"
                        },
                        {
                            "name": "owner",
                            "type": "address"
                        },
                        {
                            "name": "poolId",
                            "type": "uint128"
                        },
                        {
                            "name": "sharesAmount",
                            "type": "uint256"
                        },
                        {
                            "name": "minOut",
                            "type": "uint256"
                        },
                        {
                            "name": "nonce",
                            "type": "uint256"
                        },
                        {
                            "name": "deadline",
                            "type": "uint256"
                        },
                        {
                            "name": "extraSignatureData",
                            "type": "bytes"
                        }
                    ],
                    "EIP712Domain": [
                        {
                            "name": "name",
                            "type": "string"
                        },
                        {
                            "name": "version",
                            "type": "string"
                        },
                        {
                            "name": "verifyingContract",
                            "type": "address"
                        }
                    ]
                },
                "domain": {
                    "name": "Reya",
                    "version": "1",
                    "verifyingContract": "0xb4b77d6180cc14472a9a7bdff01cc2459368d413"
                },
                "primaryType": "RemoveLiquidityBySig",
                "message": {
                    "verifyingChainId": "1729",
                    "caller": "0xcd2869d1eb1bc8991bc55de9e9b779e912faf736",
                    "owner": self.account.address,
                    "poolId": "1",
                    "sharesAmount": self.withdraw_balance_wei,
                    "minOut": "0",
                    "nonce": "1",
                    "deadline": self.deadline,
                    "extraSignatureData": f"0x000000000000000000000000{self.account.address[2:]}000000000000000000000000000000000000000000000000000000000000a4b10000000000000000000000000000000000000000000000000000000000989680"
                }
            }

            signed_message = Account.sign_message(encode_typed_data(full_message=message), self.private_key)
            logger.info(f'#{self.id} | {self.account.address} Success create message | '
                        f'Balance - {round(self.withdraw_balance, 1)} USDC')
            return signed_message

        logger.info(f'#{self.id} | {self.account.address} Cant create message | '
                    f'Balance - {round(self.withdraw_balance, 1)} USDC')
        return None

    async def get_balance(self):
        async with aiohttp.ClientSession(headers={
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'ru-RU,ru;q=0.5',
            'origin': 'https://app.reya.xyz',
            'priority': 'u=1, i',
            'referer': 'https://app.reya.xyz/',
            'sec-ch-ua': '"Chromium";v="148", "Brave";v="148", "Not/A)Brand";v="99"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-site',
            'sec-gpc': '1',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
                          'Chrome/148.0.0.0 Safari/537.36',
        }) as client:
            self.client = client
            response: aiohttp.ClientResponse = await self.client.get(
                f'https://api.reya.xyz/api/lp-pools/1/withdraw-balance',
                params={'address': self.account.address},
                proxy=self.proxy,
            )
            response_json: dict = await response.json()
            self.withdraw_balance = float(response_json['accountShareBalance'])
            self.withdraw_balance_wei = int(self.withdraw_balance * 10 ** 30)
            self.withdraw_balance_wei = (self.withdraw_balance_wei // 10 ** 20) * 10 ** 20

    async def withdraw(self):
        async with aiohttp.ClientSession(headers={
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'ru-RU,ru;q=0.5',
            'content-type': 'application/json',
            'origin': 'https://app.reya.xyz',
            'priority': 'u=1, i',
            'referer': 'https://app.reya.xyz/',
            'sec-ch-ua': '"Chromium";v="148", "Brave";v="148", "Not/A)Brand";v="99"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-site',
            'sec-gpc': '1',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
                          'Chrome/148.0.0.0 Safari/537.36',
        }) as client:

            signed_message = await Worker.create_message_for_withdraw(self)
            if not signed_message:
                return

            v_int = signed_message.v
            if v_int < 27:
                v_int += 27
            v_padded = hex(v_int)[2:].zfill(64)

            r = hex(signed_message.r)[2:].zfill(64)
            s = hex(signed_message.s)[2:].zfill(64)

            addr = self.account.address.lower()[2:].zfill(64)

            data = (
                f"0x8cac191d"
                f"{addr}"  # 1. caller
                f"0000000000000000000000000000000000000000000000000000000000000001"  # 2. poolId
                f"{hex(int(self.withdraw_balance_wei))[2:].zfill(64)}"  # 3. sharesAmount
                f"0000000000000000000000000000000000000000000000000000000000000000"  # 4. minOut
                f"{v_padded}"  # 5. v из подписи
                f"{r}"  # 6. r из подписи
                f"{s}"  # 7. s из подписи
                f"{hex(self.deadline)[2:].zfill(64)}"  # 8. deadline
                f"0000000000000000000000000000000000000000000000000000000000989680"  # 9. 10000000
                f"000000000000000000000000000000000000000000000000000000000000a4b1"  # 10. 42161
                f"{addr}"  # 11. owner (extra)
            )

            json_data = {
                'txData': {
                    'to': '0xcd2869d1eb1bc8991bc55de9e9b779e912faf736',
                    'data': data,
                },
                'contractAddress': '0xcd2869d1eb1bc8991bc55de9e9b779e912faf736',
                'metadata': {
                    'destinationType': 'pool',
                    'clientSentTimestamp': int(time.time() * 1000),
                    'clientTimezone': random.choice(['Europe/Rome', 'Europe/Madrid', 'Europe/Amsterdam',
                                                     'Europe/Berlin', 'Europe/Paris', 'Europe/Prague']),
                },
            }
            self.client = client
            response: aiohttp.ClientResponse = await self.client.post(
                f'https://api.reya.xyz/api/transaction-gelato/executeReya',
                json=json_data,
                proxy=self.proxy,
            )
            response_json: dict = await response.json()
            tx_hash = response_json['txHash']
            logger.success(f'#{self.id} | {self.account.address} Success withdraw  tx hash - {tx_hash}')

    @async_error_handler('send_to_cex')
    async def send_to_cex(self):
        async def get_balance():
            return await self.contract_erc20.functions.balanceOf(self.account.address).call()

        if self.cex_address is None:
            logger.info(f'#{self.id} | Cex address not found')
            return

        balance = await get_balance()
        logger.info(f'#{self.id} | {self.account.address} | '
                    f'Balance - {balance} USDC | {self.private_key} | {self.cex_address}')
        if balance == 0:
            logger.info(f'#{self.id} | Balance is 0 USDC')
            return

        data = f'0xa9059cbb' \
               f'{self.cex_address.strip()[2:].zfill(64)}' \
               f'{hex(balance)[2:].zfill(64)}'

        await Worker.send_tx(self, data=data, to='0xaf88d065e77c8cC2239327C5EDb3A432268e5831')

    async def send_tx(self, data: str = None, to: str = None) -> bool:
        try:
            tx_data = {
                "chainId": 42161,
                "from": self.account.address,
                "to": self.w3_arb.to_checksum_address(to),
                "nonce": await self.w3_arb.eth.get_transaction_count(self.account.address),
                "value": 0,
                "data": data,
                'gasPrice': int(await self.w3_arb.eth.gas_price * 1.05),
                "gas": await self.w3_arb.eth.estimate_gas({
                    "from": self.account.address,
                    "to": self.w3_arb.to_checksum_address(to),
                    "value": 0,
                    "data": data,
                }),
            }

            signed_txn = self.w3_arb.eth.account.sign_transaction(tx_data, self.private_key)
            tx_hash = await self.w3_arb.eth.send_raw_transaction(signed_txn.rawTransaction)
            logger.info(f'#{self.id} | send txs...')
            tx_hash = self.w3_arb.to_hex(tx_hash)
            await asyncio.sleep(6)

            receipt = await self.w3_arb.eth.get_transaction_receipt(tx_hash)
            if receipt['status'] == 1:
                logger.success(f'#{self.id} | Success send tx | hash: {tx_hash}')
                return True

            else:
                logger.error(f'#{self.id} | Failed send tx | hash: {tx_hash}')
                return False

        except Exception as e:
            logger.error(f'#{self.id} | {e}')


async def start_work(account: list, id_acc: int, semaphore) -> None:
    async with semaphore:
        acc = Worker(private_key=account[0], proxy=account[1], cex_address=account[2], number_acc=id_acc)

        try:
            if choice == 'Send to CEX':
                await acc.send_to_cex()
            elif choice == 'Withdraw rUSD':
                await acc.withdraw()
        except Exception as e:
            logger.error(f'ID account:{id_acc} Failed: {str(e)}')


async def main() -> None:
    semaphore: asyncio.Semaphore = asyncio.Semaphore(1)  # колличество потоков

    tasks: list[asyncio.Task] = [
        asyncio.create_task(coro=start_work(account=account, id_acc=idx, semaphore=semaphore))
        for idx, account in enumerate(accounts, start=1)
    ]
    await asyncio.gather(*tasks)


if __name__ == '__main__':
    with open('accounts_data.xlsx', 'rb') as file:
        exel = pd.read_excel(file)

    # choice = questionary.select(
    #     "Select work mode:",
    #     choices=[
    #         "Withdraw rUSD",
    #         "Send to CEX",
    #         "Exit",
    #     ]
    # ).ask()
    choice = "Send to CEX"
    if 'Exit' in choice:
        exit()

    accounts: list[list] = [
        [
            row["Private key"],
            row["Proxy"] if isinstance(row["Proxy"], str) else None,
            row["Cex address"] if isinstance(row["Cex address"], str) else None,
        ]
        for index, row in exel.iterrows()
    ]
    logger.info(f'My channel: https://t.me/CryptoMindYep')
    logger.info(f'Total wallets: {len(accounts)}\n')

    asyncio.run(main())

    logger.success('The work completed')
    logger.info('Thx for donat: 0x5AfFeb5fcD283816ab4e926F380F9D0CBBA04d0e')
