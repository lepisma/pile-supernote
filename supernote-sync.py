"""Keep syncing (one-way) supernote data via local browsing URL to provided
directory at regular frequency.

Usage:
  supernote-sync.py <output-dir> --url=<url> [--poll-interval=<poll-interval>]

Options:
  --url=<url>                          Full URL for supernote web browsing tool
  --poll-interval=<poll-interval>      Duration (in s) to sleep between sync [default: 600]
"""

from docopt import docopt
import aiohttp
import asyncio
import re
import json
import os
from loguru import logger

__version__ = "0.1.1"


async def download_file(url: str, output_path: str):
    # Supernote links don't allow streaming downloads so we can't do much beyond
    # what wget can do.
    proc = await asyncio.create_subprocess_exec("wget", "-O", output_path, url, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    await proc.communicate()


async def read_directory(session: aiohttp.ClientSession, url: str) -> list[dict]:
    """Read directory specified by the url and return a list of items that could
    be either files or directories.
    """

    async with session.get(url) as response:
        html = await response.text()
        data = None

        for line in html.splitlines():
            match = re.search(r"const json = '(.*?)'", line)
            if match:
                data = json.loads(match.groups()[0])
                break

        if not data:
            raise RuntimeError(f"Unable to parse {url}")

        return data["fileList"]


async def worker(q, session, root_url, semaphore, output_dir):
    while True:
        item = await q.get()
        item_url = root_url + item["uri"]
        parent_dir = os.path.abspath(os.path.join(output_dir + item["uri"], os.pardir))
        os.makedirs(parent_dir, exist_ok=True)

        if item["isDirectory"]:
            for it in await read_directory(session, item_url):
                await q.put(it)
        else:
            async with semaphore:
                filepath = os.path.join(parent_dir, item["name"])
                await download_file(item_url, filepath)
                print(f"Downloaded {item['name']} to {filepath}")

        q.task_done()


async def supernote_to_local(session: aiohttp.ClientSession, root_url: str, output_dir: str):
    """Run supernote to local sync. This unconditionally downloads the
    supernote tree structure to the local directory.

    This overrides files with same name and path in local but doesn't delete
    anything else that might be present in local.
    """

    n = 5
    q = asyncio.Queue()
    semaphore = asyncio.Semaphore(n)

    for it in await read_directory(session, root_url):
        await q.put(it)

    tasks = []
    for _ in range(n):
        task = asyncio.create_task(worker(q, session, root_url, semaphore, output_dir))
        tasks.append(task)

    await q.join()

    for task in tasks:
        task.cancel()


async def main():
    args = docopt(__doc__, version=__version__)
    root_url = args["--url"]
    output_dir = args["<output-dir>"]
    poll_interval = int(args["--poll-interval"])

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                await supernote_to_local(session, root_url, output_dir)
            except aiohttp.client_exceptions.ClientConnectorError:
                logger.error("Can't connect to supernote")

            logger.info(f"Polling after {poll_interval} seconds")
            await asyncio.sleep(poll_interval)


if __name__ == "__main__":
    asyncio.run(main())
