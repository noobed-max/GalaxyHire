import Icon from "../../shared/components/Icon";
import type { ApiFetch } from "../../types";
import { EmailOutcomeSettings } from "../settings/panels/EmailOutcomeSettings";

export function EmailMonitoringView({ api }: { api: ApiFetch }) {
  return (
    <main className="gh-page gh-email-page scroll">
      <div className="gh-email-page-inner">
        <section className="gh-email-intro">
          <div className="gh-email-intro-copy">
            <span className="eyebrow">Automatic application tracking</span>
            <h2>Let your inbox update your applications.</h2>
            <p>
              Connect the email address you use for applications. GalaxyHire can recognize
              acknowledgements, interview invitations, offers, and rejections, then update the
              matching application for you.
            </p>
          </div>
          <div className="gh-email-steps" aria-label="How email updates work">
            <div>
              <span><Icon name="mail" size={16} /></span>
              <strong>Connect</strong>
              <small>Use a revocable app password.</small>
            </div>
            <div>
              <span><Icon name="search" size={16} /></span>
              <strong>Match</strong>
              <small>Only applications you engaged with are considered.</small>
            </div>
            <div>
              <span><Icon name="check" size={16} /></span>
              <strong>Update</strong>
              <small>Your application status stays current.</small>
            </div>
          </div>
        </section>

        <EmailOutcomeSettings api={api} />
      </div>
    </main>
  );
}
