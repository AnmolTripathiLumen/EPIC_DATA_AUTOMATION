import requests
import base64
import os
import json
import re
import sys
import time
from datetime import datetime, timedelta
from collections import defaultdict
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import gc
from urllib.parse import quote


# ============================================================
# PROJECT CONFIGS
# ============================================================
PROJECT_CONFIGS = [
    {
        "name": "CTLEP",
        "code": "CTLEP",
        "jql": 'project = "CTLEP" AND Status not in ("Canceled / Rejected", Cancel, Cancelled, Analyzing, Backlog)',
        "top_level_type": "portfolio epic",
        "progress_key": "completed_pes",
        "hierarchy_levels": {"portfolio epic", "capability", "epic (feature)", "epic", "feature", "story", "user story"},
        "parent_types": {"portfolio epic", "capability", "epic (feature)", "epic", "feature", "story", "user story"},
    },
    {
        "name": "Quantum Fiber Value Stream",
        "code": "QFVS",
        "jql": 'project = "QFVS" AND Status not in ("Canceled / Rejected", Cancel, Cancelled, Analyzing, Backlog)',
        "top_level_type": "capability",
        "progress_key": "completed_caps",
        "hierarchy_levels": {"capability", "epic (feature)", "epic", "feature", "story", "user story"},
        "parent_types": {"capability", "epic (feature)", "epic", "feature", "story", "user story"},
    },
    {
        "name": "CTL Value Stream",
        "code": "CTLVS",
        "jql": 'project = "CTL Value Stream" AND status NOT IN ("Canceled / Rejected", Cancel, Cancelled, Analyzing, Backlog)',
        "top_level_type": "capability",
        "progress_key": "completed_caps",
        "hierarchy_levels": {"capability", "epic (feature)", "epic", "feature", "story", "user story"},
        "parent_types": {"capability", "epic (feature)", "epic", "feature", "story", "user story"},
    },
]


# ============================================================
# GLOBAL CONFIG
# ============================================================
EMAIL = os.environ.get("JIRA_EMAIL", "").strip()
TOKEN = os.environ.get("JIRA_API_TOKEN", "").strip()
DOMAIN = os.environ.get("JIRA_DOMAIN", "lumen.atlassian.net").strip()
BASE_OUTPUT_FOLDER = os.environ.get("OUTPUT_FOLDER", "/tmp/output")
SP_REFRESH_TOKEN = os.environ.get("SP_REFRESH_TOKEN", "").strip()
SP_SITE_HOST = "centurylink.sharepoint.com"
SP_SITE_PATH = "/sites/MMSME"
MSAL_CLIENT_ID = "1950a258-227b-4e31-a9cf-717495945fc2"
MSAL_AUTHORITY = "https://login.microsoftonline.com/organizations"
GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]
MAX_CRAWL_DEPTH = 6
REQUESTED_FIELDS = ["*all"]
PAGE_SIZE = 100
REQUEST_TIMEOUT = 120
JIRA_OFFSET = timedelta(hours=5, minutes=30)
MAX_WORKERS = 5
API_DELAY = 0.1

log_lock = threading.Lock()


# ============================================================
# LOGGING
# ============================================================
_current_log_file = None


def log_message(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = "[" + timestamp + "] " + message
    with log_lock:
        print(log_line)
        if _current_log_file:
            try:
                with open(_current_log_file, "a", encoding="utf-8") as f:
                    f.write(log_line + "\n")
            except Exception:
                pass


# ============================================================
# PROGRESS TRACKING (resume after crash)
# ============================================================
def load_progress(progress_file):
    if os.path.exists(progress_file):
        try:
            with open(progress_file, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_progress(progress, progress_file):
    with open(progress_file, "w") as f:
        json.dump(progress, f, indent=2)


def clear_progress(progress_file):
    if os.path.exists(progress_file):
        os.remove(progress_file)


# ============================================================
# SYNC TRACKING
# ============================================================
def get_last_sync_time(last_sync_file):
    if os.path.exists(last_sync_file):
        try:
            with open(last_sync_file, "r") as f:
                data = json.load(f)
                return data.get("last_sync_time")
        except Exception:
            return None
    return None


def save_last_sync_time(sync_time, last_sync_file):
    with open(last_sync_file, "w") as f:
        json.dump({
            "last_sync_time": sync_time,
            "last_sync_readable": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }, f, indent=2)


# ============================================================
# HELPERS
# ============================================================
def sanitize_filename(text, max_len=40):
    # Replace non-ASCII chars first
    text = text.encode('ascii', 'replace').decode('ascii').replace('?', '_')
    text = re.sub(r'[<>:"/\\|?*#%\[\]&+{}~]', '', text)
    text = re.sub(r'\.{2,}', '_', text)
    text = re.sub(r'[\s\-]+', '_', text).strip('_.')
    return text[:max_len]


def get_headers():
    auth = base64.b64encode((EMAIL + ":" + TOKEN).encode()).decode()
    return {
        "Authorization": "Basic " + auth,
        "Accept": "application/json"
    }


def get_session():
    s = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


thread_local = threading.local()


def get_thread_session():
    if not hasattr(thread_local, "session"):
        thread_local.session = get_session()
    return thread_local.session


def safe_json(response):
    if not response.content:
        return None
    try:
        return response.json()
    except Exception:
        return None


def extract_text(node):
    text = ""
    if isinstance(node, dict):
        if "text" in node:
            text += node["text"] + " "
        if "content" in node:
            for child in node["content"]:
                text += extract_text(child)
    elif isinstance(node, list):
        for item in node:
            text += extract_text(item)
    return text.strip()


def get_issue_type(issue):
    fields = issue.get("fields", {})
    it = fields.get("issuetype", {})
    return it.get("name", "Other") if it else "Other"


# ============================================================
# API FUNCTIONS (with parallel support)
# ============================================================
MAX_PAGE_RETRIES = 3


def fetch_jql_page(jql, fields, max_results, next_page_token=None):
    sess = get_thread_session()
    url = "https://" + DOMAIN + "/rest/api/3/search/jql"
    body = {
        "jql": jql,
        "fields": fields,
        "maxResults": max_results
    }
    if next_page_token:
        body["nextPageToken"] = next_page_token

    headers = get_headers()
    headers["Content-Type"] = "application/json"

    for attempt in range(1, MAX_PAGE_RETRIES + 1):
        try:
            time.sleep(API_DELAY)
            response = sess.post(url, headers=headers, json=body, timeout=REQUEST_TIMEOUT)
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 30))
                log_message("  Rate limited (429). Waiting " + str(retry_after) + "s...")
                time.sleep(retry_after)
                continue
            if response.status_code != 200:
                log_message("  API error: " + str(response.status_code) + " - " + response.text[:200])
                if attempt < MAX_PAGE_RETRIES:
                    time.sleep(2 ** attempt)
                    continue
                return None
            return safe_json(response)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            log_message("  Connection error (attempt " + str(attempt) + "/" + str(MAX_PAGE_RETRIES) + "): " + str(e)[:100])
            if attempt < MAX_PAGE_RETRIES:
                time.sleep(2 ** attempt)
            else:
                return None
    return None


def get_project_issues(jql):
    issues = []
    next_page_token = None
    consecutive_failures = 0
    max_consecutive_failures = 5

    while True:
        data = fetch_jql_page(jql, REQUESTED_FIELDS, PAGE_SIZE, next_page_token)
        if not data:
            consecutive_failures += 1
            if consecutive_failures >= max_consecutive_failures:
                log_message("  WARN: " + str(max_consecutive_failures) + " consecutive page failures. Stopping with " + str(len(issues)) + " issues fetched.")
                break
            log_message("  WARN: Page fetch failed (" + str(consecutive_failures) + "/" + str(max_consecutive_failures) + "). Retrying...")
            time.sleep(5)
            continue

        consecutive_failures = 0
        batch = data.get("issues", [])
        if not batch:
            break

        issues.extend(batch)
        log_message("  Fetched " + str(len(issues)) + " issues so far...")

        next_page_token = data.get("nextPageToken")
        if not next_page_token:
            break

    return issues


def get_single_issue(issue_key):
    sess = get_thread_session()
    url = "https://" + DOMAIN + "/rest/api/3/issue/" + issue_key
    time.sleep(API_DELAY)
    response = sess.get(url, headers=get_headers(), timeout=REQUEST_TIMEOUT)
    if response.status_code != 200:
        return None
    return safe_json(response)


def get_issues_batch_parallel(keys):
    all_issues = []
    batch_size = 50

    def fetch_batch(batch_keys):
        jql_keys = ", ".join(batch_keys)
        data = fetch_jql_page(
            "key in (" + jql_keys + ")",
            REQUESTED_FIELDS,
            len(batch_keys)
        )
        if data:
            return data.get("issues", [])
        results = []
        for k in batch_keys:
            issue = get_single_issue(k)
            if issue:
                results.append(issue)
        return results

    batches = [keys[i:i + batch_size] for i in range(0, len(keys), batch_size)]

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_batch, batch): batch for batch in batches}
        for future in as_completed(futures):
            try:
                result = future.result()
                all_issues.extend(result)
            except Exception as e:
                log_message("  Warning: Batch fetch error: " + str(e))

    return all_issues


def get_children_by_parent_parallel(parent_keys):
    all_children = []
    batch_size = 10

    def fetch_children(batch):
        jql_keys = ", ".join(batch)
        jql = "parent in (" + jql_keys + ")"
        children = []
        next_page_token = None
        consecutive_failures = 0
        while True:
            data = fetch_jql_page(jql, REQUESTED_FIELDS, 100, next_page_token)
            if not data:
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    break
                time.sleep(2)
                continue
            consecutive_failures = 0
            issues = data.get("issues", [])
            children.extend(issues)
            next_page_token = data.get("nextPageToken")
            if not next_page_token:
                break
        return children

    batches = [parent_keys[i:i + batch_size] for i in range(0, len(parent_keys), batch_size)]

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_children, batch): batch for batch in batches}
        for future in as_completed(futures):
            try:
                result = future.result()
                all_children.extend(result)
            except Exception as e:
                log_message("  Warning: Children fetch error: " + str(e))

    return all_children


# ============================================================
# HIERARCHY HELPERS
# ============================================================
def collect_linked_keys(issue):
    keys = set()
    fields = issue.get("fields", {})

    if fields.get("parent"):
        keys.add(fields["parent"]["key"])

    for st in fields.get("subtasks", []):
        k = st.get("key")
        if k:
            keys.add(k)

    for link in fields.get("issuelinks", []):
        lt = link.get("type", {})
        link_name = lt.get("name", "").lower()
        outward_desc = lt.get("outward", "").lower()
        inward_desc = lt.get("inward", "").lower()

        is_hierarchy = any(kw in link_name for kw in ("parent", "child", "hierarchy", "epic"))
        is_parent_outward = any(kw in outward_desc for kw in ("parent of", "epic for", "is parent"))
        is_child_inward = any(kw in inward_desc for kw in ("child of", "in epic", "is child", "belongs to"))

        if is_hierarchy or is_parent_outward or is_child_inward:
            if "outwardIssue" in link:
                keys.add(link["outwardIssue"]["key"])
            if "inwardIssue" in link:
                keys.add(link["inwardIssue"]["key"])

    for cf in ("customfield_10014", "customfield_10008", "customfield_10100"):
        val = fields.get(cf)
        if val and isinstance(val, str) and re.match(r'^[A-Z]+-\d+$', val):
            keys.add(val)

    return keys


def detect_parent_child(issue, issue_by_key, parent_of):
    key = issue.get("key", "UNKNOWN")
    fields = issue.get("fields", {})

    if fields.get("parent"):
        pkey = fields["parent"]["key"]
        if key not in parent_of:
            parent_of[key] = pkey

    for link in fields.get("issuelinks", []):
        lt = link.get("type", {})
        link_name = lt.get("name", "").lower()
        outward_desc = lt.get("outward", "").lower()
        inward_desc = lt.get("inward", "").lower()

        is_hierarchy = any(kw in link_name for kw in ("parent", "child", "hierarchy", "epic"))
        is_parent_outward = any(kw in outward_desc for kw in ("parent of", "epic for", "is parent"))
        is_child_inward = any(kw in inward_desc for kw in ("child of", "in epic", "is child", "belongs to"))

        if "outwardIssue" in link:
            linked_key = link["outwardIssue"]["key"]
            if is_hierarchy or is_parent_outward:
                if linked_key not in parent_of:
                    parent_of[linked_key] = key

        if "inwardIssue" in link:
            linked_key = link["inwardIssue"]["key"]
            if is_hierarchy or is_child_inward:
                if key not in parent_of:
                    parent_of[key] = linked_key

    for cf in ("customfield_10014", "customfield_10008", "customfield_10100"):
        val = fields.get(cf)
        if val and isinstance(val, str) and re.match(r'^[A-Z]+-\d+$', val) and key not in parent_of:
            parent_of[key] = val
            break


# ============================================================
# CRAWL HIERARCHY (CHUNKED, PARALLEL)
# ============================================================
def crawl_hierarchy_for_chunk(seed_issues, parent_types, max_depth=MAX_CRAWL_DEPTH):
    issue_by_key = {}
    for issue in seed_issues:
        issue_by_key[issue["key"]] = issue

    queried_parents = set()

    for depth in range(1, max_depth + 1):
        new_in_this_level = set()

        unfetched_links = set()
        for issue in issue_by_key.values():
            for linked_key in collect_linked_keys(issue):
                if linked_key not in issue_by_key:
                    unfetched_links.add(linked_key)

        if unfetched_links:
            log_message("    Level " + str(depth) + "a: Fetching " + str(len(unfetched_links)) + " linked issues...")
            new_issues = get_issues_batch_parallel(list(unfetched_links))
            for issue in new_issues:
                k = issue.get("key")
                if k and k not in issue_by_key:
                    issue_by_key[k] = issue
                    new_in_this_level.add(k)

        parents_to_query = []
        for k in list(issue_by_key.keys()):
            if k not in queried_parents:
                itype = get_issue_type(issue_by_key[k]).lower()
                if itype in parent_types:
                    parents_to_query.append(k)
                queried_parents.add(k)

        if parents_to_query:
            log_message("    Level " + str(depth) + "b: Querying children of " + str(len(parents_to_query)) + " parents...")
            children = get_children_by_parent_parallel(parents_to_query)
            for issue in children:
                k = issue.get("key")
                if k and k not in issue_by_key:
                    issue_by_key[k] = issue
                    new_in_this_level.add(k)

        if new_in_this_level:
            cross_projects = set(k.split("-")[0] for k in new_in_this_level if "-" in k)
            log_message("    Level " + str(depth) + ": +" + str(len(new_in_this_level)) + " from " + ", ".join(sorted(cross_projects)))
        else:
            break

    return issue_by_key


# ============================================================
# WRITE ISSUE FILE
# ============================================================
def write_issue_file(file_path, issue, parent_of, children_map, issue_by_key):
    key = issue.get("key", "UNKNOWN")
    fields = issue.get("fields", {})

    summary = fields.get("summary", "")
    status = fields.get("status", {}).get("name", "") if fields.get("status") else ""
    issue_type = get_issue_type(issue)
    priority = fields.get("priority", {}).get("name", "") if fields.get("priority") else ""
    assignee = fields.get("assignee", {}).get("displayName", "") if fields.get("assignee") else "N/A"
    reporter = fields.get("reporter", {}).get("displayName", "") if fields.get("reporter") else "N/A"
    creator = fields.get("creator", {}).get("displayName", "") if fields.get("creator") else "N/A"
    created = fields.get("created", "")
    updated = fields.get("updated", "")
    due_date = fields.get("duedate", "N/A") or "N/A"
    resolution = fields.get("resolution", {}).get("name", "") if fields.get("resolution") else "N/A"
    labels = ", ".join(fields.get("labels", [])) or "N/A"
    components = ", ".join(c.get("name", "") for c in fields.get("components", [])) or "N/A"
    fix_versions = ", ".join(v.get("name", "") for v in fields.get("fixVersions", [])) or "N/A"
    affects_versions = ", ".join(v.get("name", "") for v in fields.get("versions", [])) or "N/A"
    description = extract_text(fields.get("description", {})) or "No description."

    project_info = fields.get("project", {})
    project_key = project_info.get("key", "") if project_info else ""
    project_name = project_info.get("name", "") if project_info else ""

    parent_data = fields.get("parent", {})
    parent_text = json.dumps(parent_data, indent=2) if parent_data else "N/A"

    p_key = parent_of.get(key, "")
    p_summary = ""
    if p_key and p_key in issue_by_key:
        p_summary = issue_by_key[p_key].get("fields", {}).get("summary", "")

    child_issues = []
    for child_key in children_map.get(key, []):
        ci = issue_by_key.get(child_key)
        if ci:
            ci_type = get_issue_type(ci)
            ci_status = ci.get("fields", {}).get("status", {}).get("name", "") if ci.get("fields", {}).get("status") else ""
            ci_summary = ci.get("fields", {}).get("summary", "")
            child_issues.append("  " + child_key + " [" + ci_type + "] [" + ci_status + "] " + ci_summary)

    comment_data = fields.get("comment", {})
    comments = []
    if isinstance(comment_data, dict):
        for c in comment_data.get("comments", []):
            author = c.get("author", {}).get("displayName", "")
            body = extract_text(c.get("body", {}))
            date = c.get("created", "")
            comments.append("  [" + date + "] " + author + ": " + body)

    links = []
    for link in fields.get("issuelinks", []):
        link_type_out = link.get("type", {}).get("outward", "")
        link_type_in = link.get("type", {}).get("inward", "")
        if "outwardIssue" in link:
            lk = link["outwardIssue"].get("key", "")
            ls = link["outwardIssue"].get("fields", {}).get("summary", "")
            lst = ""
            if link["outwardIssue"].get("fields", {}).get("status"):
                lst = link["outwardIssue"]["fields"]["status"].get("name", "")
            links.append("  " + link_type_out + " " + lk + " [" + lst + "]: " + ls)
        elif "inwardIssue" in link:
            lk = link["inwardIssue"].get("key", "")
            ls = link["inwardIssue"].get("fields", {}).get("summary", "")
            lst = ""
            if link["inwardIssue"].get("fields", {}).get("status"):
                lst = link["inwardIssue"]["fields"]["status"].get("name", "")
            links.append("  " + link_type_in + " " + lk + " [" + lst + "]: " + ls)

    subtasks = []
    for st in fields.get("subtasks", []):
        st_key = st.get("key", "")
        st_summary = st.get("fields", {}).get("summary", "")
        st_status = st.get("fields", {}).get("status", {}).get("name", "") if st.get("fields", {}).get("status") else ""
        subtasks.append("  " + st_key + " [" + st_status + "]: " + st_summary)

    attachments = []
    for att in fields.get("attachment", []):
        att_name = att.get("filename", "")
        att_size = att.get("size", 0)
        att_author = att.get("author", {}).get("displayName", "")
        att_created = att.get("created", "")
        attachments.append("  " + att_name + " (" + str(att_size) + " bytes) by " + att_author + " on " + att_created)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write("=" * 70 + "\n")
        f.write("  JIRA ISSUE: " + key + "\n")
        f.write("  PROJECT: " + project_key + " (" + project_name + ")\n")
        f.write("  URL: https://" + DOMAIN + "/browse/" + key + "\n")
        f.write("  Last Synced: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n")
        f.write("=" * 70 + "\n")

        f.write("-" * 40 + "\n")
        f.write("STANDARD FIELDS\n")
        f.write("-" * 40 + "\n")
        f.write("Summary: " + summary + "\n")
        f.write("Issue Type: " + issue_type + "\n")
        f.write("Status: " + status + "\n")
        f.write("Priority: " + priority + "\n")
        f.write("Resolution: " + resolution + "\n")
        f.write("Assignee: " + assignee + "\n")
        f.write("Reporter: " + reporter + "\n")
        f.write("Creator: " + creator + "\n")
        f.write("Created: " + created + "\n")
        f.write("Updated: " + updated + "\n")
        f.write("Due Date: " + due_date + "\n")
        f.write("Labels: " + labels + "\n")
        f.write("Components: " + components + "\n")
        f.write("Fix Versions: " + fix_versions + "\n")
        f.write("Affects Versions: " + affects_versions + "\n")
        f.write("Parent: " + parent_text + "\n")
        if p_key:
            f.write("Hierarchy Parent: " + p_key + " - " + p_summary + "\n")

        f.write("-" * 40 + "\n")
        f.write("DESCRIPTION\n")
        f.write("-" * 40 + "\n")
        f.write(description + "\n")

        f.write("-" * 40 + "\n")
        f.write("COMMENTS\n")
        f.write("-" * 40 + "\n")
        f.write("\n".join(comments) + "\n" if comments else "No comments.\n")

        f.write("-" * 40 + "\n")
        f.write("LINKED ISSUES\n")
        f.write("-" * 40 + "\n")
        f.write("\n".join(links) + "\n" if links else "No linked issues.\n")

        if child_issues:
            f.write("-" * 40 + "\n")
            f.write("CHILD ISSUES (" + str(len(child_issues)) + ")\n")
            f.write("-" * 40 + "\n")
            f.write("\n".join(child_issues) + "\n")

        f.write("-" * 40 + "\n")
        f.write("SUBTASKS\n")
        f.write("-" * 40 + "\n")
        f.write("\n".join(subtasks) + "\n" if subtasks else "No subtasks.\n")

        f.write("-" * 40 + "\n")
        f.write("ATTACHMENTS\n")
        f.write("-" * 40 + "\n")
        f.write("\n".join(attachments) + "\n" if attachments else "No attachments.\n")

        f.write("=" * 70 + "\n")
        f.write("  End of " + key + "\n")
        f.write("=" * 70 + "\n")


# ============================================================
# PROCESS ONE TOP-LEVEL ITEM (CHUNKED)
# ============================================================
def process_top_level_item(item_issue, project_folder, hierarchy_levels, parent_types, sp_uploader=None):
    item_key = item_issue["key"]
    item_summary = item_issue.get("fields", {}).get("summary", "")
    log_message("  Processing: " + item_key + " - " + item_summary)

    chunk_issues = crawl_hierarchy_for_chunk([item_issue], parent_types)
    log_message("    Total issues in tree: " + str(len(chunk_issues)))

    parent_of = {}
    for issue in chunk_issues.values():
        detect_parent_child(issue, chunk_issues, parent_of)

    children_map = defaultdict(list)
    for child, parent in parent_of.items():
        children_map[parent].append(child)

    def get_folder_path(key, visited=None):
        if visited is None:
            visited = set()
        if key in visited:
            return []
        visited.add(key)

        issue = chunk_issues.get(key)
        if not issue:
            return []

        itype = get_issue_type(issue).lower()
        summary = issue.get("fields", {}).get("summary", "")
        folder_name = key + "_" + sanitize_filename(summary)

        path_above = []
        if key in parent_of:
            path_above = get_folder_path(parent_of[key], visited)

        if itype in hierarchy_levels:
            return path_above + [folder_name]
        else:
            return path_above

    written = 0
    written_files = []
    for key, issue in chunk_issues.items():
        issue_type = get_issue_type(issue)
        summary = issue.get("fields", {}).get("summary", "")
        folder_parts = get_folder_path(key)

        if folder_parts:
            own_type = issue_type.lower()
            if own_type in hierarchy_levels:
                type_path = os.path.join(project_folder, *folder_parts)
            else:
                type_folder = sanitize_filename(issue_type) if issue_type else "Other"
                type_path = os.path.join(project_folder, *folder_parts, type_folder)
        else:
            type_folder = sanitize_filename(issue_type) if issue_type else "Other"
            type_path = os.path.join(project_folder, "_other", type_folder)

        os.makedirs(type_path, exist_ok=True)

        safe_summary = sanitize_filename(summary)
        filename = key + "_" + safe_summary + ".txt" if safe_summary else key + ".txt"
        file_path = os.path.join(type_path, filename)

        write_issue_file(file_path, issue, parent_of, children_map, chunk_issues)
        written += 1
        written_files.append(file_path)
        if sp_uploader:
            sp_uploader.queue_files([file_path])

    log_message("    Saved " + str(written) + " files")

    del chunk_issues, parent_of, children_map
    gc.collect()

    return written, written_files


# ============================================================
# PROCESS NON-HIERARCHY ISSUES
# ============================================================
def process_other_issues(issues, project_folder, sp_uploader=None):
    log_message("  Processing " + str(len(issues)) + " non-hierarchy issues...")

    parent_of = {}
    issue_by_key = {i["key"]: i for i in issues}
    for issue in issues:
        detect_parent_child(issue, issue_by_key, parent_of)

    children_map = defaultdict(list)
    for child, parent in parent_of.items():
        children_map[parent].append(child)

    written = 0
    written_files = []
    for issue in issues:
        key = issue["key"]
        issue_type = get_issue_type(issue)
        summary = issue.get("fields", {}).get("summary", "")
        type_folder = sanitize_filename(issue_type) if issue_type else "Other"
        type_path = os.path.join(project_folder, "_other", type_folder)
        os.makedirs(type_path, exist_ok=True)

        safe_summary = sanitize_filename(summary)
        filename = key + "_" + safe_summary + ".txt" if safe_summary else key + ".txt"
        file_path = os.path.join(type_path, filename)

        write_issue_file(file_path, issue, parent_of, children_map, issue_by_key)
        written += 1
        written_files.append(file_path)
        if sp_uploader:
            sp_uploader.queue_files([file_path])

    log_message("    Saved " + str(written) + " non-hierarchy files")

    del issue_by_key, parent_of, children_map
    gc.collect()

    return written, written_files


# ============================================================
# SHAREPOINT UPLOAD (via Microsoft Graph API)
# ============================================================
def get_graph_access_token():
    import msal
    app = msal.PublicClientApplication(MSAL_CLIENT_ID, authority=MSAL_AUTHORITY)
    result = app.acquire_token_by_refresh_token(SP_REFRESH_TOKEN, scopes=GRAPH_SCOPE)
    if "access_token" in result:
        return result["access_token"]
    log_message("  ERROR getting Graph token: " + result.get("error_description", "Unknown error"))
    return None


def get_site_id(headers):
    resp = requests.get(
        "https://graph.microsoft.com/v1.0/sites/" + SP_SITE_HOST + ":" + SP_SITE_PATH,
        headers=headers, timeout=30
    )
    if resp.status_code == 200:
        return resp.json()["id"]
    log_message("  ERROR looking up SharePoint site: HTTP " + str(resp.status_code))
    return None


def upload_to_sharepoint(local_folder, sp_folder_prefix):
    try:
        access_token = get_graph_access_token()
        if not access_token:
            log_message("  Skipping SharePoint upload: could not get access token")
            return

        headers = {"Authorization": "Bearer " + access_token}
        token_acquired_at = time.time()

        site_id = get_site_id(headers)
        if not site_id:
            return

        # Upload checkpoint file - tracks successfully uploaded files
        upload_checkpoint_file = os.path.join(local_folder, "_upload_checkpoint.json")
        uploaded_set = set()
        if os.path.exists(upload_checkpoint_file):
            try:
                with open(upload_checkpoint_file, "r") as f:
                    uploaded_set = set(json.load(f))
                log_message("  Resuming upload: " + str(len(uploaded_set)) + " files already uploaded")
            except Exception:
                uploaded_set = set()

        # Collect all files to upload
        files_to_upload = []
        for root, dirs, files_list in os.walk(local_folder):
            for f in files_list:
                if f.startswith("_"):
                    continue
                local_path = os.path.join(root, f)
                relative_path = os.path.relpath(local_path, local_folder).replace("\\", "/")
                sp_path = sp_folder_prefix + "/" + relative_path
                if relative_path in uploaded_set:
                    continue  # Skip already uploaded
                files_to_upload.append((local_path, sp_path, relative_path))

        total_files = len(files_to_upload)
        already_done = len(uploaded_set)
        log_message("  SharePoint upload: " + str(total_files) + " files to upload" +
                    (" (" + str(already_done) + " already done)" if already_done else ""))

        if total_files == 0:
            log_message("  All files already uploaded.")
            # Clean checkpoint on completion
            if os.path.exists(upload_checkpoint_file):
                os.remove(upload_checkpoint_file)
            return

        uploaded = [0]
        failed = [0]
        token_lock = threading.Lock()
        checkpoint_lock = threading.Lock()
        token_state = {"token": access_token, "acquired": token_acquired_at}

        # Use a session with connection pooling for faster uploads
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=20, pool_maxsize=20, max_retries=0
        )
        session.mount("https://", adapter)

        def get_current_headers():
            with token_lock:
                # Refresh token every 45 minutes
                if time.time() - token_state["acquired"] > 2700:
                    log_message("  Refreshing SharePoint access token...")
                    new_token = get_graph_access_token()
                    if new_token:
                        token_state["token"] = new_token
                        token_state["acquired"] = time.time()
                return {"Authorization": "Bearer " + token_state["token"],
                        "Content-Type": "application/octet-stream"}

        def save_upload_checkpoint():
            with checkpoint_lock:
                with open(upload_checkpoint_file, "w") as f:
                    json.dump(list(uploaded_set), f)

        def sanitize_sp_path(path):
            """Sanitize and URL-encode SharePoint path segments."""
            parts = path.split("/")
            cleaned = []
            for part in parts:
                part = re.sub(r'[#%\[\]&+{}\\~"*:<>|]', '_', part)
                part = part.encode('ascii', 'replace').decode('ascii').replace('?', '_')
                part = part.strip('. ')
                if not part:
                    part = '_'
                part = quote(part, safe='')
                cleaned.append(part)
            return "/".join(cleaned)

        def upload_single_file(item):
            local_path, sp_path, relative_path = item
            sp_path = sanitize_sp_path(sp_path)
            try:
                with open(local_path, "rb") as f:
                    file_data = f.read()

                upload_url = (
                    "https://graph.microsoft.com/v1.0/sites/" + site_id +
                    "/drive/root:/" + sp_path + ":/content"
                )

                for attempt in range(1, 4):
                    h = get_current_headers()
                    resp = session.put(upload_url, headers=h, data=file_data, timeout=60)

                    if resp.status_code in (200, 201):
                        with checkpoint_lock:
                            uploaded_set.add(relative_path)
                        return True
                    elif resp.status_code == 429:
                        retry_after = int(resp.headers.get("Retry-After", 30))
                        time.sleep(retry_after)
                    elif resp.status_code == 400 and attempt == 3:
                        err_body = resp.text[:200] if resp.text else 'no body'
                        log_message("  WARN: Failed to upload " + os.path.basename(local_path) + " (HTTP 400: " + err_body + ")")
                        return False
                    elif attempt < 3:
                        time.sleep(2 ** attempt)
                    else:
                        log_message("  WARN: Failed to upload " + os.path.basename(local_path) + " (HTTP " + str(resp.status_code) + ")")
                        return False
            except Exception as e:
                log_message("  WARN: Error uploading " + os.path.basename(local_path) + ": " + str(e))
                return False
            return False

        # Upload with 20 parallel workers, checkpoint every 50 files
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = {executor.submit(upload_single_file, item): item for item in files_to_upload}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    uploaded[0] += 1
                else:
                    failed[0] += 1
                done = uploaded[0] + failed[0]
                if done % 50 == 0:
                    save_upload_checkpoint()
                    log_message("  SharePoint progress: " + str(uploaded[0]) + " uploaded, " + str(failed[0]) + " failed / " + str(total_files) + " total")

        # Final checkpoint save
        save_upload_checkpoint()
        session.close()
        log_message("  SharePoint upload complete: " + str(uploaded[0]) + " uploaded, " + str(failed[0]) + " failed, " + str(total_files) + " total")

        # Clean checkpoint if all succeeded
        if failed[0] == 0:
            if os.path.exists(upload_checkpoint_file):
                os.remove(upload_checkpoint_file)
    except Exception as e:
        log_message("  ERROR uploading to SharePoint: " + str(e))


# ============================================================
# STREAMING UPLOADER - uploads files as they are fetched
# ============================================================
class StreamingUploader:
    def __init__(self, project_folder, sp_folder_prefix):
        self.project_folder = project_folder
        self.sp_folder_prefix = sp_folder_prefix
        self.ready = False
        self.uploaded = 0
        self.failed = 0
        self.total_queued = 0

        self.token_lock = threading.Lock()
        self.checkpoint_lock = threading.Lock()
        self.counter_lock = threading.Lock()

        # Load checkpoint
        self.checkpoint_file = os.path.join(project_folder, "_upload_checkpoint.json")
        self.uploaded_set = set()
        if os.path.exists(self.checkpoint_file):
            try:
                with open(self.checkpoint_file, "r") as f:
                    self.uploaded_set = set(json.load(f))
                log_message("  Streaming upload: resuming, " + str(len(self.uploaded_set)) + " files already uploaded")
            except Exception:
                self.uploaded_set = set()

        # Initialize SharePoint session
        try:
            access_token = get_graph_access_token()
            if not access_token:
                return
            headers = {"Authorization": "Bearer " + access_token}
            self.site_id = get_site_id(headers)
            if not self.site_id:
                return
            self.token_state = {"token": access_token, "acquired": time.time()}
            self.session = requests.Session()
            adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=0)
            self.session.mount("https://", adapter)
            self.executor = ThreadPoolExecutor(max_workers=20)
            self.futures = []
            self.ready = True
        except Exception as e:
            log_message("  ERROR initializing streaming upload: " + str(e))

    def _get_headers(self):
        with self.token_lock:
            if time.time() - self.token_state["acquired"] > 2700:
                log_message("  Refreshing SharePoint access token...")
                new_token = get_graph_access_token()
                if new_token:
                    self.token_state["token"] = new_token
                    self.token_state["acquired"] = time.time()
            return {"Authorization": "Bearer " + self.token_state["token"],
                    "Content-Type": "application/octet-stream"}

    def _sanitize_sp_path(self, path):
        parts = path.split("/")
        cleaned = []
        for part in parts:
            part = re.sub(r'[#%\[\]&+{}\\~"*:<>|]', '_', part)
            part = part.encode('ascii', 'replace').decode('ascii').replace('?', '_')
            part = part.strip('. ')
            if not part:
                part = '_'
            part = quote(part, safe='')
            cleaned.append(part)
        return "/".join(cleaned)

    def _save_checkpoint(self):
        with self.checkpoint_lock:
            with open(self.checkpoint_file, "w") as f:
                json.dump(list(self.uploaded_set), f)

    def _upload_one(self, local_path, sp_path, relative_path):
        if relative_path in self.uploaded_set:
            return True
        sp_path = self._sanitize_sp_path(sp_path)
        try:
            with open(local_path, "rb") as f:
                file_data = f.read()
            if len(file_data) == 0:
                log_message("  WARN: Skipping empty file " + relative_path)
                return False
            upload_url = ("https://graph.microsoft.com/v1.0/sites/" + self.site_id +
                          "/drive/root:/" + sp_path + ":/content")
            for attempt in range(1, 4):
                h = self._get_headers()
                resp = self.session.put(upload_url, headers=h, data=file_data, timeout=60)
                if resp.status_code in (200, 201):
                    with self.checkpoint_lock:
                        self.uploaded_set.add(relative_path)
                    return True
                elif resp.status_code == 400:
                    err_body = resp.text[:200] if resp.text else 'no body'
                    log_message("  WARN: Upload failed " + relative_path + " (HTTP 400: " + err_body + ")")
                    return False
                elif resp.status_code == 409:
                    if attempt < 3:
                        time.sleep(2 ** attempt)
                    else:
                        log_message("  WARN: Upload failed " + relative_path + " (HTTP 409: conflict)")
                        return False
                elif resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 30))
                    time.sleep(retry_after)
                elif attempt < 3:
                    time.sleep(2 ** attempt)
                else:
                    log_message("  WARN: Upload failed " + relative_path + " (HTTP " + str(resp.status_code) + ")")
                    return False
        except Exception as e:
            log_message("  WARN: Upload error " + relative_path + ": " + str(e))
            return False
        return False

    def queue_files(self, file_paths):
        for local_path in file_paths:
            if os.path.basename(local_path).startswith("_"):
                continue
            relative_path = os.path.relpath(local_path, self.project_folder).replace("\\", "/")
            if relative_path in self.uploaded_set:
                continue
            sp_path = self.sp_folder_prefix + "/" + relative_path
            future = self.executor.submit(self._upload_one, local_path, sp_path, relative_path)
            self.futures.append(future)
            self.total_queued += 1

        # Check completed futures and log progress
        done_futures = [f for f in self.futures if f.done()]
        for f in done_futures:
            if f.result():
                self.uploaded += 1
            else:
                self.failed += 1
            self.futures.remove(f)

        if self.uploaded > 0 and self.uploaded % 50 == 0:
            self._save_checkpoint()
            log_message("  Upload progress: " + str(self.uploaded) + " uploaded, " + str(self.failed) + " failed")

    def wait_and_close(self):
        log_message("  Waiting for remaining uploads to complete...")
        for future in as_completed(self.futures):
            if future.result():
                self.uploaded += 1
            else:
                self.failed += 1
            done = self.uploaded + self.failed
            if done % 100 == 0:
                self._save_checkpoint()
                log_message("  Upload progress: " + str(self.uploaded) + " uploaded, " + str(self.failed) + " failed")

        self._save_checkpoint()
        self.session.close()
        self.executor.shutdown(wait=False)
        log_message("  SharePoint upload complete: " + str(self.uploaded) + " uploaded, " + str(self.failed) + " failed")

        if self.failed == 0 and os.path.exists(self.checkpoint_file):
            os.remove(self.checkpoint_file)


# ============================================================
# RUN ONE PROJECT
# ============================================================
def run_project(config):
    global _current_log_file

    project_name = config["name"]
    project_code = config["code"]
    jql_base = config["jql"]
    top_level_type = config["top_level_type"]
    progress_key = config["progress_key"]
    hierarchy_levels = config["hierarchy_levels"]
    parent_types = config["parent_types"]

    # 1. Setup paths
    output_folder = os.path.join(BASE_OUTPUT_FOLDER, project_code)
    os.makedirs(output_folder, exist_ok=True)
    last_sync_file = os.path.join(output_folder, "_last_sync.json")
    log_file = os.path.join(output_folder, "_sync_log.txt")
    progress_file = os.path.join(output_folder, "_progress.json")
    sp_folder_base = os.environ.get("SP_FOLDER_BASE", "SolutionForge/Jira_CloudRun").strip().rstrip("/")
    sp_folder_prefix = sp_folder_base + "/" + project_code

    _current_log_file = log_file

    log_message("=" * 60)
    log_message("  Jira " + project_code + " -> OneDrive Sync (No Exclusions)")
    log_message("  ALL issue types will be fetched")
    log_message("  Cross-project crawl enabled")
    log_message("  Parallel workers: " + str(MAX_WORKERS))
    log_message("=" * 60)

    project_folder = output_folder

    # 2. Check last_sync time
    last_sync = get_last_sync_time(last_sync_file)
    sync_start_time = (datetime.now() - JIRA_OFFSET).strftime("%Y-%m-%d %H:%M")

    # Load progress FIRST to check if we are resuming from a crash
    progress = load_progress(progress_file)
    is_resuming = bool(progress.get(progress_key))

    # 3. Build JQL (incremental if last_sync exists)
    if last_sync:
        jql = jql_base + ' AND updated >= "' + last_sync + '"'
        log_message("MODE: Incremental sync (last sync: " + last_sync + ")")
    else:
        jql = jql_base

        # 4. Clean old files if full sync (first run) and NOT resuming
        if is_resuming:
            log_message("MODE: Resuming interrupted full sync")
            log_message("  " + str(len(progress[progress_key])) + " top-level items already completed - skipping cleanup")
        else:
            log_message("MODE: Full sync (first run)")
            log_message("  Cleaning old files...")
            for root, dirs, files in os.walk(project_folder, topdown=False):
                for name in files:
                    if name in ("_last_sync.json", "_sync_log.txt", "_progress.json"):
                        continue
                    try:
                        os.remove(os.path.join(root, name))
                    except PermissionError:
                        pass
                for name in dirs:
                    try:
                        os.rmdir(os.path.join(root, name))
                    except (PermissionError, OSError):
                        pass

    log_message("JQL: " + jql)

    # 5. Fetch all issues using config["jql"]
    log_message("")
    log_message("Step 1: Fetching " + project_name + " issues...")
    all_issues = get_project_issues(jql)
    log_message("  Total " + project_code + " issues: " + str(len(all_issues)))

    if not all_issues:
        log_message("  No issues found. Exiting.")
        save_last_sync_time(sync_start_time, last_sync_file)
        return

    # 6. Separate top-level items from others
    top_level_items = []
    other_issues = []
    for issue in all_issues:
        if get_issue_type(issue).lower() == top_level_type:
            top_level_items.append(issue)
        else:
            other_issues.append(issue)

    top_level_label = top_level_type.title() + "s"
    log_message("  " + top_level_label + ": " + str(len(top_level_items)))
    log_message("  Other issues: " + str(len(other_issues)))

    # 7. Process each top-level item with crash-resume
    completed_items = set(progress.get(progress_key, []))

    # Setup streaming upload if SharePoint is configured
    sp_uploader = None
    if SP_REFRESH_TOKEN:
        sp_uploader = StreamingUploader(project_folder, sp_folder_prefix)
        if sp_uploader.ready:
            log_message("  Streaming upload enabled: files upload as they are fetched")
        else:
            log_message("  Streaming upload failed to initialize, will skip uploads")
            sp_uploader = None

    log_message("")
    log_message("Step 2: Processing " + top_level_label + " (one at a time)...")
    total_written = 0

    for idx, item in enumerate(top_level_items, 1):
        item_key = item["key"]

        if item_key in completed_items:
            log_message("  [" + str(idx) + "/" + str(len(top_level_items)) + "] Skipping " + item_key + " (already done)")
            continue

        log_message("")
        log_message("  [" + str(idx) + "/" + str(len(top_level_items)) + "] " + item_key)
        try:
            written, file_paths = process_top_level_item(item, project_folder, hierarchy_levels, parent_types, sp_uploader)
            total_written += written

            completed_items.add(item_key)
            progress[progress_key] = list(completed_items)
            save_progress(progress, progress_file)

        except Exception as e:
            log_message("  ERROR processing " + item_key + ": " + str(e))
            log_message("  Saving progress and continuing...")
            save_progress(progress, progress_file)
            continue

    # 8. Process other issues
    if other_issues:
        log_message("")
        log_message("Step 3: Processing non-hierarchy issues...")
        written, file_paths = process_other_issues(other_issues, project_folder, sp_uploader)
        total_written += written

    # 9. Wait for all uploads to finish
    if sp_uploader:
        sp_uploader.wait_and_close()

    # 10. Save sync time, clear progress
    save_last_sync_time(sync_start_time, last_sync_file)
    clear_progress(progress_file)

    log_message("")
    log_message("=" * 60)
    log_message("  Sync complete!")
    log_message("  Total files written: " + str(total_written))
    log_message("  Output: " + output_folder)
    log_message("  Next sync will fetch changes after: " + sync_start_time)
    log_message("=" * 60)


# ============================================================
# MAIN
# ============================================================
def main():
    if not EMAIL or not TOKEN:
        print("ERROR: JIRA_EMAIL and JIRA_API_TOKEN environment variables must be set.")
        print("Set them via: export JIRA_EMAIL=your-email; export JIRA_API_TOKEN=your-token")
        sys.exit(1)

    overall_start = time.time()

    print("=" * 60)
    print("  EPIC DATA AUTOMATION - Multi-Project Jira Sync")
    print("  Projects: " + ", ".join(c["code"] for c in PROJECT_CONFIGS))
    print("  Output: " + BASE_OUTPUT_FOLDER)
    print("=" * 60)

    for config in PROJECT_CONFIGS:
        project_start = time.time()
        try:
            run_project(config)
            elapsed = round(time.time() - project_start, 1)
            print("[" + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "] Finished: " + config["name"] + " (" + str(elapsed) + "s)")
        except Exception as e:
            print("[" + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "] ERROR in " + config["name"] + ": " + str(e))
            import traceback
            traceback.print_exc()

    elapsed = round(time.time() - overall_start, 1)
    print("")
    print("=" * 60)
    print("  All projects complete! Total time: " + str(elapsed) + "s")
    print("=" * 60)


if __name__ == "__main__":
    main()
