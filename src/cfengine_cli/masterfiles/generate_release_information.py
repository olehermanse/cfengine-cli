import json
from cfengine_cli.masterfiles.download import (
    ENTERPRISE_RELEASES_URL,
    COMMUNITY_RELEASES_URL,
    download_all_versions,
)
from cfengine_cli.masterfiles.generate_vcf_download import generate_vcf_download
from cfengine_cli.masterfiles.analyze import get_stable_releases, sort_release_data
from cfengine_cli.masterfiles.generate_vcf_git_checkout import generate_vcf_git_checkout
from cfengine_cli.masterfiles.check_download_matches_git import (
    check_download_matches_git,
)
from cfengine_cli.masterfiles.generate_git_tags import (
    generate_git_tags_map,
)
from cfbs.utils import (
    get_json,
    immediate_subdirectories,
    version_is_at_least,
    write_json,
    CFBSNetworkError,
    CFBSExitError,
)

DOWNLOAD_PATH = "downloaded_masterfiles"


def generate_release_information_impl(
    omit_download=False, check=False, min_version=None
):
    if not omit_download:
        print("Downloading masterfiles...")

        downloaded_versions = download_all_versions(DOWNLOAD_PATH, min_version)

        print("Download finished. Every reported checksum matches.")
    else:
        downloaded_versions = immediate_subdirectories(DOWNLOAD_PATH)

        downloaded_versions = list(
            filter(
                lambda v: version_is_at_least(v, min_version),
                downloaded_versions,
            )
        )

    print(
        "Downloading releases of masterfiles from cfengine.com and generating release information..."
    )
    generate_vcf_download(DOWNLOAD_PATH, downloaded_versions)

    generate_release_history()

    generate_git_tags_map()

    if check:
        print(
            "Downloading releases of masterfiles from git (github.com) and generating "
            + "additional release information for comparison..."
        )
        generate_vcf_git_checkout(downloaded_versions)
        print("Candidate release information generated.")
        print("Comparing files from cfengine.com and github.com...")

        check_download_matches_git(downloaded_versions)

        print("The masterfiles downloaded from github.com and cfengine.com match.")
    else:
        print("Release information successfully generated.")
        print("See the results in ./masterfiles/")
        print(
            "(Run again with --check-against-git to download and compare with files "
            + "from git, and generate -git.json files)"
        )


def download_enterprise_releasedata():
    # Downloading releases.json:
    try:
        releases_data = get_json(ENTERPRISE_RELEASES_URL)

    except CFBSNetworkError:
        raise CFBSExitError(
            "Downloading CFEngine release data failed - check your Wi-Fi / network settings."
        )

    return releases_data


def download_community_releasedata():
    # Downloading community/releases.json
    try:
        releases_data = get_json(COMMUNITY_RELEASES_URL)

    except CFBSNetworkError:
        raise CFBSExitError(
            "Downloading CFEngine release data failed - check your Wi-Fi / network settings."
        )

    return releases_data


def process_release_type(folder, download_func):
    # Function for processing either community or enterprise releases
    release_data = download_func()

    write_json_pretty(f"./{folder}/releases.json", release_data)

    stable_releases = get_stable_releases(release_data)

    file_checksums_dict = build_release_history(stable_releases)

    write_version_files(stable_releases, folder)

    sorted_releases = sort_release_data(file_checksums_dict)

    write_json(f"./{folder}/checksums.json", sorted_releases)


def generate_release_history():
    print("Generating release history information...")
    process_release_type("cfengine-enterprise", download_enterprise_releasedata)
    process_release_type("cfengine-community", download_community_releasedata)


def build_release_history(filtered_releases):
    release_history = {}

    for release_data in filtered_releases:
        if not release_data.get("version") or not release_data.get("URL"):
            continue

        subdata, version = download_release_version_data(release_data)
        version_files = extract_version_files(subdata)

        if version_files:
            release_history[version] = version_files

    return release_history


def download_release_version_data(release_data):
    # Downloads each versionnumber.json in releases.json
    version = release_data.get("version")
    url = release_data.get("URL")

    try:
        return get_json(url), version
    except CFBSNetworkError:
        raise CFBSExitError(
            f"Downloading CFEngine release data for version {version} failed - check your Wi-Fi / network settings."
        )


def extract_version_files(subdata):
    # Gets filenames and checksums for each file in the subdata of releases.json:
    artifacts = subdata.get("artifacts", {})
    version_files = {}

    for asset_list in artifacts.values():
        for asset_data in asset_list:
            filename, checksum = extract_file_info(asset_data)

            if filename and checksum:
                version_files[filename] = checksum

    return version_files


def extract_file_info(asset_data):
    url = asset_data.get("URL")
    checksum = asset_data.get("SHA256")

    if url and checksum:
        filename = url.split("/")[-1]
        return filename, checksum

    return None, None


def write_version_files(stable_releases, folder):
    # Writes versionfiles for each version
    for release_data in stable_releases:
        version = release_data.get("version")
        if not version:
            continue
        version_data, _ = download_release_version_data(release_data)
        write_json(f"./{folder}/versions/{version}.json", version_data)


def write_json_pretty(path, data):
    # Writes release information in same format as on cfengine.com
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
