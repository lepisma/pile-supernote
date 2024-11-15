"""Keep syncing (one-way) supernote data via local browsing URL to provided
directory.

Usage:
  supernote-sync.py <output-dir> [--url=<url>] [--no-conversion]

Options:
  --url=<url>                          Full URL for supernote web browsing tool. If this is not provided,
                                       autodiscovery is attempted.
  --no-conversion                      Don't do any PDF or other format-appropriate conversions after
                                       downloading
"""

from docopt import docopt
import aiohttp
import aiofiles
import asyncio
import re
import json
import os
from loguru import logger
import networkscan
import supernotelib as sn
from supernotelib.converter import PdfConverter


__version__ = "0.2.0"


async def is_supernote_url(session: aiohttp.ClientSession, url: str) -> bool:
    try:
        await read_directory(session, url)
        return True
    except Exception:
        return False


async def discover_supernote(hosts: list[str]) -> str:
    """Discover Supernote with local browsing enabled on the network.

    We assume the server has opened 8089 port, on which we do a simple
    validation.
    """

    logger.debug(f"Scanning {len(hosts)} hosts: {hosts}")

    async with aiohttp.ClientSession() as session:
        port = 8089
        tasks = [is_supernote_url(session, f"http://{host}:{port}") for host in hosts]

        results = await asyncio.gather(*tasks)

    idx, result = max(enumerate(results), key=lambda it: it[1])

    if result is False:
        raise RuntimeError("Supernote was not found on the network")

    return f"http://{hosts[idx]}:{port}"


async def convert_to_pdf(input_path: str, output_path: str):
    """Convert input .note file to output .pdf file.
    """

    loop = asyncio.get_event_loop()

    def _convert(input_path: str):
        notebook = sn.load_notebook(input_path, policy="strict")
        converter = PdfConverter(notebook, palette=None)
        return converter.convert(-1, vectorize=False, enable_link=True, enable_keyword=True)

    data = await loop.run_in_executor(None, _convert, input_path)

    async with aiofiles.open(output_path, "wb") as fp:
        await fp.write(data)


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


async def worker(q, session, root_url, semaphore, output_dir, should_convert: bool):
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
                logger.info(f"Downloaded {item['name']} to {filepath}")

                if should_convert and filepath.endswith(".note"):
                    pdf_filepath = os.path.splitext(filepath)[0] + ".pdf"
                    await convert_to_pdf(filepath, pdf_filepath)
                    logger.info(f"Converted {item['name']} to pdf")

        q.task_done()


async def supernote_to_local(session: aiohttp.ClientSession, root_url: str, output_dir: str, should_convert: bool):
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
        task = asyncio.create_task(worker(q, session, root_url, semaphore, output_dir, should_convert))
        tasks.append(task)

    await q.join()

    for task in tasks:
        task.cancel()


async def main(root_url: str, output_dir: str, should_convert: bool):
    async with aiohttp.ClientSession() as session:
        try:
            await supernote_to_local(session, root_url, output_dir, should_convert)
        except aiohttp.client_exceptions.ClientConnectorError:
            logger.error("Can't connect to supernote")


if __name__ == "__main__":
    args = docopt(__doc__, version=__version__)

    output_dir = args["<output-dir>"]
    should_convert = not args["--no-conversion"]
    root_url = args["--url"]

    if not root_url:
        logger.info("Auto discovering Supernote on the network")
        # Running this outside the loop since it conflicts with an inner event loop
        network = "192.168.0.0/24"
        scan = networkscan.Networkscan(network)
        scan.run()
        root_url = asyncio.run(discover_supernote(scan.list_of_hosts_found))
        logger.info(f"Supernote found at {root_url}")

    asyncio.run(main(root_url, output_dir, should_convert))
