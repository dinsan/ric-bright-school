from enum import StrEnum

# Grades 1-3 sign in with pictures, so picture sign-in is on by default for those classes.
PICTURE_SIGN_IN_MAX_GRADE = 3
SCHOOL_YEAR_PATTERN = r"^\d{4}-\d{4}$"  # e.g. "2026-2027"
# Failed class-code guesses per IP before a lockout (codes are ~40 bits; this stops scans).
CLASS_CODE_IP_THRESHOLD = 20


class EnrollmentStatus(StrEnum):
    ACTIVE = "active"
    REMOVED = "removed"
