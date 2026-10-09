# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

import argparse
import os
import sys

import requests
from bs4 import BeautifulSoup

TIMEOUT = 60  # seconds, for every request

def download_results(year: int, category: str):
    """Downloads the verified result files (.xml.bz2) of the verifiers that
    scored in the category into the directory results_<year>_<category>.
    Returns the list of result files that could not be downloaded."""
    base_url = f"https://sv-comp.sosy-lab.org/{year}/results/results-verified/"

    response = requests.get(base_url, timeout=TIMEOUT)
    response.raise_for_status()
    html_data = response.text
    soup = BeautifulSoup(html_data, "html.parser")

    row_id = category  # Replace with the actual ID you are searching for
    table_row = soup.find('tr', id=row_id)

    if not table_row:
        raise Exception("Could not find the specified category")

    value_cells = table_row.find_all('td', class_='value')
    result_paths = []
    for cell in value_cells:
        a_element = cell.find('a')
        if a_element and int(a_element.text.strip()) > 0:  # Check if the <a> element exists
            result_download_path = a_element['href'].strip()
            result_download_path = result_download_path.removesuffix(".table.html")
            result_paths.append(result_download_path)

    download_folder = f"results_{year}_{category}"

    # Create the directory if it doesn't exist
    if not os.path.exists(download_folder):
        os.makedirs(download_folder)

    failed = []
    for result_path in result_paths:
        new_url = base_url + result_path
        try:
            response = requests.get(new_url, timeout=TIMEOUT)
            response.raise_for_status()

            file_name = os.path.join(download_folder, result_path)

            with open(file_name, 'wb') as f:
                f.write(response.content)

            print(f"Downloaded: {file_name}")

        except requests.exceptions.RequestException as e:
            print(f"Error downloading {new_url}: {e}", file=sys.stderr)
            failed.append(result_path)

    return failed


# Example usage
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download SV-COMP results.")
    parser.add_argument(
        "--year", type=int, default=2025, help="SV-COMP year (e.g., 2025)"
    )
    parser.add_argument(
        "--category", type=str,
        default="no-data-race.NoDataRace-Main",
        help="Category for which results are needed (e.g., no-data-race.NoDataRace-Main)"
    )
    args = parser.parse_args()

    failed = download_results(args.year, args.category)
    if failed:
        sys.exit(f"{len(failed)} result file(s) could not be downloaded: {', '.join(failed)}")
