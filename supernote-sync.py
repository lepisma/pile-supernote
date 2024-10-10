"""Sync (one-way) supernote data via local browsing URL to provided directory.

Usage:
  supernote-sync.py <output-dir> --url=<url>

Options:
  --url=<url>         Full URL for supernote web browsing tool
"""

from docopt import docopt, printable_usage
import aiohttp
import asyncio
import re
import json
import os

__version__ = "0.1.0"


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


async def main():
    args = docopt(__doc__, version=__version__)
    root_url = args["--url"]
    output_dir = args["<output-dir>"]

    n = 5
    q = asyncio.Queue()
    semaphore = asyncio.Semaphore(n)

    async with aiohttp.ClientSession() as session:
        for it in await read_directory(session, root_url):
            await q.put(it)

        tasks = []
        for _ in range(n):
            task = asyncio.create_task(worker(q, session, root_url, semaphore, output_dir))
            tasks.append(task)

        await q.join()

        for task in tasks:
            task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
