"""Gradescope (no public API): log in with your account and read each course's assignment table.

The credentials are only sent to gradescope.com and are never logged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

from .dates import LA

BASE = "https://www.gradescope.com"


class GradescopeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Course:
    id: str
    shortname: str  # e.g. "DSC180A_FA26_001"
    name: str
    term: str       # e.g. "Fall 2026"


@dataclass(frozen=True)
class Assignment:
    course: Course
    title: str
    status: str               # "Submitted", "No Submission", or a score like "8.0 / 10.0"
    released: datetime | None
    due: datetime | None
    late_due: datetime | None
    url: str


class GradescopeClient:
    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = "Mozilla/5.0 (homework-sync)"

    def login(self, email: str, password: str) -> str:
        """Log in and return the account page HTML (which lists your courses)."""
        try:
            page = self.session.get(f"{BASE}/login", timeout=30)
            page.raise_for_status()
            token = BeautifulSoup(page.text, "lxml").find("input", {"name": "authenticity_token"})
            if token is None:
                raise GradescopeError("Gradescope's login page changed (no authenticity_token).")
            resp = self.session.post(f"{BASE}/login", timeout=30, data={
                "utf8": "✓",
                "authenticity_token": token["value"],
                "session[email]": email,
                "session[password]": password,
                "session[remember_me]": "0",
                "commit": "Log In",
                "session[remember_me_sso]": "0",
            })
            resp.raise_for_status()
        except requests.RequestException as e:
            raise GradescopeError(f"Could not reach Gradescope ({type(e).__name__}).") from None
        if "/login" in resp.url or not BeautifulSoup(resp.text, "lxml").select_one("a.courseBox"):
            raise GradescopeError(
                "Gradescope login failed. Check GRADESCOPE_EMAIL and GRADESCOPE_PASS in secrets.env. "
                "If you normally sign in with 'School Credentials' (UCSD SSO), first set a Gradescope "
                "password: Gradescope → Account → Edit Account, or use 'Forgot password' on the login page.")
        return resp.text

    def assignments(self, course: Course) -> list[Assignment]:
        try:
            resp = self.session.get(f"{BASE}/courses/{course.id}", timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise GradescopeError(f"Could not load {course.shortname} ({type(e).__name__}).") from None
        return parse_assignments(resp.text, course)


def parse_courses(html: str) -> list[Course]:
    """Courses on the account page, with the term heading each one is listed under."""
    doc = BeautifulSoup(html, "lxml")
    courses = []
    for term in doc.select(".courseList--term"):
        term_name = term.get_text(" ", strip=True)
        listing = term.find_next_sibling(class_="courseList--coursesForTerm")
        for box in listing.select("a.courseBox") if listing else []:
            shortname = box.select_one(".courseBox--shortname")
            name = box.select_one(".courseBox--name")
            courses.append(Course(
                id=box["href"].rstrip("/").split("/")[-1],
                shortname=shortname.get_text(strip=True) if shortname else "",
                name=name.get_text(" ", strip=True) if name else "",
                term=term_name,
            ))
    return courses


def parse_assignments(html: str, course: Course) -> list[Assignment]:
    table = BeautifulSoup(html, "lxml").select_one("#assignments-student-table")
    if table is None:
        return []  # no assignments posted yet
    out = []
    for row in table.select("tbody tr"):
        name_cell = row.find("th")
        if name_cell is None:
            continue
        title = name_cell.get_text(" ", strip=True)
        status = row.select_one(".submissionStatus--text")
        due_times = row.select("time.submissionTimeChart--dueDate")
        out.append(Assignment(
            course=course,
            title=title,
            status=status.get_text(" ", strip=True) if status else "",
            released=_time(row.select_one("time.submissionTimeChart--releaseDate")),
            due=_time(due_times[0]) if due_times else None,
            late_due=_time(due_times[1]) if len(due_times) > 1 else None,
            url=_link(name_cell, course),
        ))
    return out


def _time(tag: Tag | None) -> datetime | None:
    if tag is None or not tag.get("datetime"):
        return None
    try:
        return datetime.strptime(tag["datetime"], "%Y-%m-%d %H:%M:%S %z").astimezone(LA)
    except ValueError:
        return None


def _link(cell: Tag, course: Course) -> str:
    a = cell.find("a", href=True)
    if a:
        return urljoin(BASE, a["href"])
    button = cell.find(attrs={"data-assignment-id": True})
    if button:
        return f"{BASE}/courses/{course.id}/assignments/{button['data-assignment-id']}"
    return f"{BASE}/courses/{course.id}"
