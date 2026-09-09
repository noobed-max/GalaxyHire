"""Email monitoring (§7).

Three things carry the risk here, and they're what these tests concentrate on:

  * **Classification order.** Rejection emails routinely contain interview, assessment and
    acknowledgement vocabulary. Reading one as progress is the worst error this feature can make —
    the user would believe they were advancing when they had been turned down.
  * **Attribution.** A wrong lead match silently rewrites the status of an unrelated application, so
    ambiguity has to lose.
  * **Idempotence and monotonicity.** Polls overlap and threads re-deliver old text; nothing may
    double-record or walk a settled outcome backwards.
"""

from __future__ import annotations

import json

import pytest

from email_monitor.classify import (
    Outcome,
    classify_outcome,
    company_tokens,
    is_ats_domain,
    match_lead,
    sender_domain,
)
from email_monitor.client import EmailError, Message
from email_monitor.service import (
    KEY_ENABLED,
    KEY_HOST,
    KEY_PASSWORD,
    KEY_SEEN_UIDS,
    KEY_USERNAME,
    EmailMonitorService,
    load_config,
)


class TestClassificationOrder:
    """Rejections wearing other categories' clothing. This is where a naive matcher fails."""

    def test_rejection_mentioning_an_interview_is_a_rejection(self):
        verdict = classify_outcome(
            "Your application to Acme",
            "Thank you for applying. Unfortunately we will not be moving forward and won't be "
            "scheduling an interview at this time.",
        )
        assert verdict.outcome is Outcome.REJECTED

    def test_rejection_that_thanks_you_for_applying_is_a_rejection(self):
        # Nearly every rejection opens with the acknowledgement phrasing.
        verdict = classify_outcome(
            "Update on your application",
            "Thank you for your interest in Acme. We have decided to move forward with other candidates.",
        )
        assert verdict.outcome is Outcome.REJECTED

    def test_rejection_praising_your_assessment_is_a_rejection(self):
        verdict = classify_outcome(
            "Acme — update",
            "We were impressed by your technical assessment, but regret to inform you that we are "
            "pursuing other candidates.",
        )
        assert verdict.outcome is Outcome.REJECTED

    def test_offer_that_also_proposes_a_call_is_an_offer(self):
        verdict = classify_outcome(
            "Offer of employment — Acme",
            "We are delighted to offer you the role. Can we schedule a call to walk through the details?",
        )
        assert verdict.outcome is Outcome.OFFER


class TestClassification:
    @pytest.mark.parametrize(
        ("subject", "body", "expected"),
        [
            ("Interview invitation", "We would like to invite you for an interview.", Outcome.INTERVIEW),
            ("Next steps", "Please share your availability for a call next week.", Outcome.INTERVIEW),
            ("Technical challenge", "Please complete the take-home assessment via HackerRank.", Outcome.ASSESSMENT),
            ("Application received", "We have received your application and are reviewing it.", Outcome.ACKNOWLEDGED),
            ("Your order has shipped", "Your parcel is on its way.", Outcome.UNKNOWN),
            ("Newsletter", "This week in engineering.", Outcome.UNKNOWN),
        ],
    )
    def test_categories(self, subject: str, body: str, expected: Outcome):
        assert classify_outcome(subject, body).outcome is expected

    def test_a_subject_line_hit_is_higher_confidence(self):
        # Evidence in the subject beats the same phrase buried in a quoted reply chain.
        in_subject = classify_outcome("We regret to inform you", "…")
        in_body = classify_outcome("Acme", "We regret to inform you that…")
        assert in_subject.confidence == "high"
        assert in_body.confidence == "medium"

    def test_unrelated_mail_is_left_alone(self):
        assert classify_outcome("", "").outcome is Outcome.UNKNOWN


class TestSenderParsing:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("jobs@acme.com", "acme.com"),
            ("Acme Recruiting <no-reply@acme.com>", "acme.com"),
            ('"Talent, Acme" <talent@mail.acme.co.uk>', "mail.acme.co.uk"),
            ("garbage", ""),
        ],
    )
    def test_extracts_the_domain(self, raw: str, expected: str):
        assert sender_domain(raw) == expected

    def test_recognises_ats_domains_and_their_subdomains(self):
        assert is_ats_domain("greenhouse.io")
        assert is_ats_domain("us.greenhouse-mail.io")
        assert is_ats_domain("mail.lever.co")
        assert not is_ats_domain("acme.com")

    def test_company_tokens_drop_words_that_identify_nothing(self):
        # Without this, "Acme Technologies" matches every company with "technologies" in its name and
        # the outcome lands on the wrong application.
        assert company_tokens("Acme Technologies Inc") == {"acme"}
        assert company_tokens("The Software Group") == set()


def two_applied_leads() -> list[dict]:
    """Fresh dicts each call. FakeLeads mutates what it's given, so a shared list leaks state."""
    return [
        {"job_id": "j1", "company": "Acme", "url": "https://boards.greenhouse.io/acme/jobs/1", "status": "applied"},
        {"job_id": "j2", "company": "Globex", "url": "https://jobs.globex.com/apply/2", "status": "applied"},
    ]


def one_applied_lead() -> list[dict]:
    return [{"job_id": "j1", "company": "Acme", "url": "https://acme.com/jobs/1", "status": "applied"}]


class TestAttribution:

    def test_matches_on_the_domain_applied_through(self):
        match = match_lead(
            from_address="careers@jobs.globex.com", subject="Update", body="", leads=two_applied_leads()
        )
        assert match and match.job_id == "j2" and match.confidence == "high"

    def test_matches_the_company_name_in_the_sender_domain(self):
        match = match_lead(from_address="talent@acme.com", subject="Hello", body="", leads=two_applied_leads())
        assert match and match.job_id == "j1"

    def test_an_ats_sender_needs_the_company_named_in_the_message(self):
        # Several applications share one ATS, so the ATS domain alone cannot attribute anything.
        match = match_lead(
            from_address="no-reply@greenhouse.io",
            subject="Your application to Acme",
            body="",
            leads=two_applied_leads(),
        )
        assert match and match.job_id == "j1" and match.confidence == "medium"

    def test_an_ats_sender_naming_nobody_matches_nothing(self):
        assert match_lead(
            from_address="no-reply@greenhouse.io", subject="An update", body="", leads=two_applied_leads()
        ) is None

    def test_unrelated_mail_matches_nothing(self):
        # Returning None is the common, correct answer; most mail is not about a job.
        assert match_lead(
            from_address="billing@stripe.com", subject="Invoice", body="", leads=two_applied_leads()
        ) is None

    def test_no_leads_means_no_match(self):
        assert match_lead(from_address="jobs@acme.com", subject="x", body="", leads=[]) is None


# ── service ───────────────────────────────────────────────────────────────────


class FakeSettings:
    def __init__(self, values: dict | None = None):
        self.values = dict(values or {})

    def get_settings(self) -> dict:
        return dict(self.values)

    def save_settings(self, patch: dict) -> None:
        self.values.update(patch)


class FakeLeads:
    def __init__(self, leads: list[dict]):
        self._leads = leads
        self.events: list[tuple[str, str]] = []
        self.statuses: list[tuple[str, str]] = []

    def get_all_leads(self) -> list[dict]:
        return self._leads

    def get_lead_by_id(self, job_id: str) -> dict:
        return next((lead for lead in self._leads if lead["job_id"] == job_id), {})

    def record_event(self, job_id: str, action: str) -> None:
        self.events.append((job_id, action))

    def update_lead_status(self, job_id: str, status: str) -> None:
        self.statuses.append((job_id, status))
        for lead in self._leads:
            if lead["job_id"] == job_id:
                lead["status"] = status


class FakeRepo:
    def __init__(self, leads: FakeLeads, settings: FakeSettings):
        self.leads = leads
        self.settings = settings


def configured(**over) -> dict:
    base = {
        KEY_ENABLED: "true",
        KEY_HOST: "imap.example.com",
        KEY_USERNAME: "me@example.com",
        KEY_PASSWORD: "app-password",
    }
    base.update(over)
    return base


def mail(uid: str, sender: str, subject: str, body: str = "") -> Message:
    return Message(uid=uid, from_address=sender, subject=subject, body=body)


def service_with(messages: list[Message], leads: list[dict], settings: dict | None = None):
    class FakeClient:
        def __init__(self, config):
            self.config = config

        def recent(self, *, since_days=14, limit=200):
            return messages

        def check(self):
            return {"ok": True, "mailbox": "INBOX", "messages": len(messages)}

    repo = FakeRepo(FakeLeads(leads), FakeSettings(settings if settings is not None else configured()))
    return EmailMonitorService(repo, FakeClient), repo


class TestPolling:

    def test_a_rejection_moves_the_lead(self):
        svc, repo = service_with(
            [mail("1", "jobs@acme.com", "Update", "We regret to inform you that…")], one_applied_lead()
        )
        result = svc.poll()
        assert result.matched == 1 and result.updated == 1
        assert repo.leads.statuses == [("j1", "rejected")]

    def test_an_interview_moves_the_lead_to_interviewing(self):
        svc, repo = service_with(
            [mail("1", "jobs@acme.com", "Interview invitation", "We would like to invite you for an interview.")],
            one_applied_lead(),
        )
        svc.poll()
        assert repo.leads.statuses == [("j1", "interviewing")]

    def test_an_acknowledgement_records_but_does_not_advance(self):
        # "We got your application" is not progress; claiming otherwise inflates the pipeline.
        svc, repo = service_with(
            [mail("1", "jobs@acme.com", "Received", "We have received your application.")], one_applied_lead()
        )
        result = svc.poll()
        assert result.matched == 1 and result.updated == 0
        assert repo.leads.statuses == []
        assert repo.leads.events, "the event should still be recorded"

    def test_an_assessment_records_but_does_not_advance(self):
        svc, repo = service_with(
            [mail("1", "jobs@acme.com", "Challenge", "Please complete the take-home assessment.")],
            one_applied_lead(),
        )
        svc.poll()
        assert repo.leads.statuses == []
        assert any("assessment" in action for _job, action in repo.leads.events)

    def test_processed_messages_are_not_reprocessed(self):
        # Polls overlap by design, so without this one rejection appends an event every cycle.
        messages = [mail("1", "jobs@acme.com", "Update", "We regret to inform you…")]
        svc, repo = service_with(messages, one_applied_lead())
        svc.poll()
        second = svc.poll()
        assert second.skipped_seen == 1
        assert len(repo.leads.statuses) == 1

    def test_force_reprocesses_seen_messages(self):
        # Uses an acknowledgement rather than a rejection on purpose: a rejection moves the lead out
        # of the engaged set, so a second pass would correctly find nothing to match and the test
        # would prove the wrong thing.
        messages = [mail("1", "jobs@acme.com", "Received", "We have received your application.")]
        svc, _repo = service_with(messages, one_applied_lead())
        svc.poll()
        again = svc.poll(force=True)
        assert again.skipped_seen == 0 and again.matched == 1

    def test_a_settled_outcome_is_never_walked_back(self):
        # Threads re-deliver old text and mail arrives out of order.
        leads = [{"job_id": "j1", "company": "Acme", "url": "https://acme.com/j/1", "status": "accepted"}]
        svc, repo = service_with([mail("1", "jobs@acme.com", "Update", "We regret to inform you…")], leads)
        result = svc.poll()
        assert result.updated == 0
        assert repo.leads.statuses == []

    def test_unmatched_mail_changes_nothing(self):
        svc, repo = service_with([mail("1", "news@substack.com", "Weekly digest", "Hello")], one_applied_lead())
        result = svc.poll()
        assert result.matched == 0 and repo.leads.statuses == []

    def test_only_engaged_leads_are_matched_against(self):
        # A cold recruiter email must not mark a merely-discovered lead "rejected".
        leads = [{"job_id": "j9", "company": "Acme", "url": "https://acme.com/j/9", "status": "discovered"}]
        svc, _repo = service_with([mail("1", "jobs@acme.com", "Update", "We regret to inform you…")], leads)
        assert svc.poll().matched == 0

    def test_disabled_monitoring_does_nothing(self):
        svc, _repo = service_with([mail("1", "jobs@acme.com", "x", "y")], one_applied_lead(), configured(**{KEY_ENABLED: "false"}))
        result = svc.poll()
        assert result.enabled is False and result.scanned == 0

    def test_unconfigured_monitoring_reports_why(self):
        svc, _repo = service_with([], one_applied_lead(), {KEY_ENABLED: "true"})
        assert "isn't configured" in svc.poll().error

    def test_an_unreachable_server_is_reported_not_raised(self):
        class Failing:
            def __init__(self, config):
                pass

            def recent(self, **_):
                raise EmailError("Could not reach imap.example.com:993")

        repo = FakeRepo(FakeLeads(one_applied_lead()), FakeSettings(configured()))
        result = EmailMonitorService(repo, Failing).poll()
        assert result.error and "Could not reach" in result.error

    def test_a_corrupt_seen_marker_reprocesses_instead_of_crashing(self):
        svc, _repo = service_with(
            [mail("1", "jobs@acme.com", "Update", "We regret to inform you…")],
            one_applied_lead(),
            configured(**{KEY_SEEN_UIDS: "{not json"}),
        )
        assert svc.poll().matched == 1


class TestConfigAndStatus:
    def test_load_config_needs_all_three_credentials(self):
        assert load_config(configured()) is not None
        for missing in (KEY_HOST, KEY_USERNAME, KEY_PASSWORD):
            assert load_config(configured(**{missing: ""})) is None

    def test_status_never_exposes_the_password(self):
        svc, _repo = service_with([], [], configured())
        status = svc.status()
        assert status["configured"] is True
        assert status["mailbox"]["password_set"] is True
        assert "password" not in status["mailbox"]
        assert "app-password" not in json.dumps(status)
