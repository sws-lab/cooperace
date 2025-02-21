#!/usr/bin/env python3
import os
import requests
import zipfile

# TODO: REPLACE ALL THE STUFF BELOW WITH fm_tools
# from fm_tools.download import DownloadDelegate
# from fm_tools.tool_info_module import ToolInfoModule

# Create tmp directory if it doesn't exist
os.makedirs('tmp', exist_ok=True)
os.makedirs('tools', exist_ok=True)

# Base URL for the fm-tools repository
fm_tools_repo = "https://gitlab.com/sosy-lab/benchmarking/fm-tools/-/raw/main/data/"
tools = ["goblint", "dartagnan", "deagle", "uautomizer", "ugemcutter", "utaipan", "nacpa", "sv-sanitizers", "cpachecker", "racerf"]
doi_file = "tools.txt"


def get_doi(tool_name):
    url = f"{fm_tools_repo}{tool_name}.yml"
    response = requests.get(url)
    response.raise_for_status()   
    for line in response.text.splitlines():
        if "doi: " in line:
            return line.split("doi: ")[1].replace('"', "").strip()
    raise ValueError(f"DOI not found in {tool_name}.yml")

def get_download_url(doi):
    response = requests.get(f"https://doi.org/{doi}")
    response.raise_for_status()
    record_id = response.url.split('/')[-1]
    response = requests.get(f"https://zenodo.org/api/records/{record_id}")
    response.raise_for_status()
    data = response.json()
    if 'files' in data:
        return data['files'][0]['links']['self']
    raise ValueError(f"Download URL not found for DOI {doi}")

def download_and_unzip(url):
    zip_path = f"tmp/content"
    extract_to = 'tools/'
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with open(zip_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        for member in zip_ref.infolist():
            zip_ref.extract(member, extract_to)
            member_path = os.path.join(extract_to, member.filename)
            perm = member.external_attr >> 16
            if perm:
                os.chmod(member_path, perm)
    os.remove(zip_path)

# Read existing DOIs from file
if os.path.exists(doi_file):
    with open(doi_file, 'r') as f:
        existing_dois = dict(line.strip().split(': ') for line in f)
else:
    existing_dois = {}

with open(doi_file, 'w') as f:
    for tool in tools:
        doi = get_doi(tool)
        url = get_download_url(doi)
        if tool not in existing_dois or existing_dois[tool] != doi:
            print(f"Downloading {tool} from {url}")
            download_and_unzip(url)
            f.write(f"{tool}: {doi}\n")
        else:
            print(f"Skipping {tool} as it is already downloaded")
            f.write(f"{tool}: {existing_dois[tool]}\n")

# Remove the tmp directory
os.rmdir('tmp')