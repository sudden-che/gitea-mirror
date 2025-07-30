import requests
import argparse
import urllib3
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

SOURCE_URL = os.getenv("SRC_GITEA_URL")
SOURCE_TOKEN = os.getenv("SRC_GITEA_TOKEN")
TARGET_URL = os.getenv("DEST_GITEA_URL")
TARGET_TOKEN = os.getenv("DEST_GITEA_TOKEN")
TARGET_USERNAME = os.getenv("DEST_GITEA_USER")

HEADERS_SRC = {"Authorization": f"token {SOURCE_TOKEN}"}
HEADERS_TGT = {"Authorization": f"token {TARGET_TOKEN}"}

# === SSL-игнорирование ===
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# === HTTP-обёртки ===
def http_get(url, headers, verify_ssl):
    return requests.get(url, headers=headers, verify=verify_ssl)

def http_post(url, headers, payload, verify_ssl):
    return requests.post(url, headers=headers, json=payload, verify=verify_ssl)

# === Gitea API функции ===
def list_user_repos(verify_ssl):
    repos = []
    page = 1
    limit = 100
    while True:
        url = (f"{SOURCE_URL}/api/v1/user/repos"
               f"?visibility=all&affiliation=owner,collaborator,organization_member"
               f"&limit={limit}&page={page}")
        resp = http_get(url, HEADERS_SRC, verify_ssl)
        resp.raise_for_status()
        page_data = resp.json()
        if not page_data:
            break
        repos.extend(page_data)
        page += 1
    return repos


def list_orgs(verify_ssl):
    resp = http_get(f"{SOURCE_URL}/api/v1/user/orgs", HEADERS_SRC, verify_ssl)
    resp.raise_for_status()
    return resp.json()


def list_org_repos(org_name, verify_ssl):
    repos, page = [], 1
    while True:
        url = f"{SOURCE_URL}/api/v1/orgs/{org_name}/repos?limit=100&page={page}"
        resp = http_get(url, HEADERS_SRC, verify_ssl)
        resp.raise_for_status()
        page_data = resp.json()
        if not page_data:
            break
        repos.extend(page_data)
        page += 1
    return repos

def repo_exists_on_target(owner, repo, verify_ssl):
    resp = http_get(f"{TARGET_URL}/api/v1/repos/{owner}/{repo}", HEADERS_TGT, verify_ssl)
    return resp.status_code == 200

# Создание репозитория на целевом сервере
def create_repo_on_target(owner, name, desc, private, dry_run, verify_ssl):
    if dry_run:
        print(f"DRY-RUN: создаём репо {owner}/{name} на target")
        return
    endpoint = "/user/repos" if owner == get_current_user_login(verify_ssl) else f"/orgs/{owner}/repos"
    payload = {"name": name, "description": desc or "", "private": private, "auto_init": False}
    resp = http_post(f"{TARGET_URL}/api/v1{endpoint}", HEADERS_TGT, payload, verify_ssl)
    resp.raise_for_status()
    print(f"Создано репо {owner}/{name} на target")
    return resp.json()




def add_push_mirror(owner, name, remote_address, dry_run, verify_ssl):
    if dry_run:
        print(f"DRY-RUN: would add push mirror for {owner}/{name} -> {remote_address}")
        return

    payload = {
        "remote_address": remote_address,
        "remote_username": TARGET_USERNAME or "",
        "remote_password": TARGET_TOKEN or "",
        "branch_filter": "*",
        "interval": "8h0m0s",
        "sync_on_commit": True
    }
    url = f"{SOURCE_URL}/api/v1/repos/{owner}/{name}/push_mirrors"
    resp = http_post(url, HEADERS_SRC, payload, verify_ssl)
    print("POST response:", resp.status_code, resp.text)
    if resp.status_code in (400, 401, 403):
        print("❌ Ошибка при добавлении push mirror:", resp.status_code, resp.text)
    resp.raise_for_status()
    print(f"✅ Push mirror создан (id={resp.json().get('id')}) для {owner}/{name}")
    return resp.json()



# Получение существующих push-мирроров
def get_push_mirrors(owner, name, verify_ssl):
    resp = http_get(f"{SOURCE_URL}/api/v1/repos/{owner}/{name}/push_mirrors", HEADERS_SRC, verify_ssl)
    resp.raise_for_status()
    return resp.json()

# Логин текущего пользователя на source
def get_current_user_login(verify_ssl):
    resp = http_get(f"{SOURCE_URL}/api/v1/user", HEADERS_SRC, verify_ssl)
    resp.raise_for_status()
    return resp.json().get("login")

# Обработка одного репозитория
def process_repo(repo, dry_run, verify_ssl):
    full = repo.get("full_name", "")
    if "/" not in full:
        print(f"Пропускаем некорректное: {full}")
        return
    owner, name = full.split("/", 1)
    remote_address = f"{TARGET_URL}/{owner}/{name}.git"
    print(f"\nОбработка {owner}/{name}")

    if not repo_exists_on_target(owner, name, verify_ssl):
        create_repo_on_target(owner, name, repo.get("description"), repo.get("private", True), dry_run, verify_ssl)
    else:
        print(f"  Репо {owner}/{name} уже есть на target")

    if dry_run:
        return

    try:
        mirrors = http_get(f"{SOURCE_URL}/api/v1/repos/{owner}/{name}/push_mirrors", HEADERS_SRC, verify_ssl)
        if mirrors.status_code == 401 or mirrors.status_code == 403:
            print(f"  Нет доступа к push_mirrors: {mirrors.status_code}")
            return
        mirrors.raise_for_status()
        mirrors_list = mirrors.json()
    except Exception as e:
        print(f"  Ошибка при проверке зеркал: {e}")
        return

    if any(m.get("remote_address") == remote_address for m in mirrors_list):
        print(f"  Push-зеркало уже настроено (remote_address={remote_address}), пропускаем")
    else:
        add_push_mirror(owner, name, remote_address, dry_run, verify_ssl)




# Главная функция
def main():
    parser = argparse.ArgumentParser(description="Настройка push-зеркал между Gitea")
    parser.add_argument("--dry-run", action="store_true", help="только симуляция")
    parser.add_argument("--insecure", action="store_true", help="игнорировать SSL ошибки")
    args = parser.parse_args()

    verify_ssl = not args.insecure
    print(f"Dry-run: {args.dry_run}, SSL verify: {verify_ssl}")

    for repo in list_user_repos(verify_ssl):
        process_repo(repo, args.dry_run, verify_ssl)
    for org in list_orgs(verify_ssl):
        org_name = org.get("username") or org.get("login") or org.get("name")
        print(f"\nОрганизация: {org_name}")
        for repo in list_org_repos(org_name, verify_ssl):
            process_repo(repo, args.dry_run, verify_ssl)

if __name__ == "__main__":
    main()
