from datetime import datetime

from homework_sync.dates import LA
from homework_sync.gradescope import Course, parse_assignments, parse_courses

ACCOUNT = """
<div class="courseList">
  <div class="courseList--term">Fall 2026</div>
  <div class="courseList--coursesForTerm">
    <a class="courseBox" href="/courses/111"><h3 class="courseBox--shortname">CSE151B_FA26_001</h3>
      <div class="courseBox--name">CSE 151B - Deep Learning</div></a>
  </div>
  <div class="courseList--term">Spring 2026</div>
  <div class="courseList--coursesForTerm">
    <a class="courseBox" href="/courses/222"><h3 class="courseBox--shortname">CSE166_SP26_A00</h3></a>
  </div>
</div>"""

COURSE_PAGE = """
<table id="assignments-student-table"><tbody>
  <tr><th><a href="/courses/111/assignments/5/submissions/9">HW1</a></th>
    <td class="submissionStatus"><div class="submissionStatus--text">Submitted</div></td>
    <td><time class="submissionTimeChart--releaseDate" datetime="2026-09-28 00:00:00 -0700"></time>
        <time class="submissionTimeChart--dueDate" datetime="2026-10-05 23:59:00 -0700"></time>
        <time class="submissionTimeChart--dueDate" datetime="2026-10-07 23:59:00 -0700"></time></td></tr>
  <tr><th><button data-assignment-id="6">PA2</button></th>
    <td class="submissionStatus"><div class="submissionStatus--text">No Submission</div></td>
    <td><time class="submissionTimeChart--dueDate" datetime="2026-10-13 06:59:00 +0000"></time></td></tr>
  <tr><th>Quiz 1</th><td></td><td></td></tr>
</tbody></table>"""


def test_courses_are_listed_with_their_term():
    courses = parse_courses(ACCOUNT)
    assert [(c.id, c.shortname, c.term) for c in courses] == [
        ("111", "CSE151B_FA26_001", "Fall 2026"), ("222", "CSE166_SP26_A00", "Spring 2026")]


def test_assignment_rows():
    course = Course("111", "CSE151B_FA26_001", "", "Fall 2026")
    hw, pa, quiz = parse_assignments(COURSE_PAGE, course)

    assert (hw.title, hw.status) == ("HW1", "Submitted")
    assert hw.due == datetime(2026, 10, 5, 23, 59, tzinfo=LA)
    assert hw.late_due == datetime(2026, 10, 7, 23, 59, tzinfo=LA)
    assert hw.url == "https://www.gradescope.com/courses/111/assignments/5/submissions/9"

    assert pa.due == datetime(2026, 10, 12, 23, 59, tzinfo=LA)  # UTC converted to Pacific
    assert pa.url == "https://www.gradescope.com/courses/111/assignments/6"

    assert (quiz.title, quiz.due, quiz.status) == ("Quiz 1", None, "")


def test_course_without_assignments():
    assert parse_assignments("<p>No assignments yet</p>", Course("1", "X", "", "")) == []


def test_export_rows_share_one_format():
    from datetime import date

    from homework_sync.export_csv import as_datetime, row, sort_rows

    canvas_row = row("MGT 16", "Quiz #1", as_datetime(date(2026, 10, 19)), "https://canvas")
    assert (canvas_row["Due"], canvas_row["Day"], canvas_row["Status"]) == ("2026-10-19 23:59", "Mon", "")
    undated = row("CSE 151B", "Quiz 1", None, "https://gs")
    early = row("CSE 151B", "HW2", datetime(2026, 10, 12, 23, 59, tzinfo=LA), "https://gs", status="Submitted")
    assert [r["Assignment"] for r in sort_rows([undated, canvas_row, early])] == ["HW2", "Quiz #1", "Quiz 1"]
