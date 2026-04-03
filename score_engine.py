"""
Civic Responsibility Score Engine
Calculates citizen loyalty scores based on social media monitoring.
Higher scores = more government-aligned behavior = more privileges.
Lower scores = critical/dissident behavior = restricted access to services.

Scoring tiers:
  800-1000  EXEMPLARY   - Priority access to housing, loans, travel
  700-799   TRUSTED     - Full privileges
  500-699   NORMAL      - Standard access
  300-499   SUSPICIOUS  - Under monitoring, limited services
  0-299     DISSIDENT   - Travel banned, employment revoked, flagged for detention
"""

import datetime
import logging

logger = logging.getLogger(__name__)

# Positive behaviors (regime-aligned)
SHARE_GOVT_CONTENT_BONUS = 15
REPORT_NEIGHBOR_BONUS = 25
ATTEND_RALLY_BONUS = 30
PRAISE_LEADER_BONUS = 20
VOLUNTEER_STATE_PROGRAM_BONUS = 35
DELETE_CRITICAL_POST_BONUS = 20  # Self-censorship reward

# Negative behaviors (dissident indicators)
POST_CRITICISM_PENALTY = -40
JOIN_PROTEST_PENALTY = -60
FOLLOW_DISSIDENT_PENALTY = -20
SHARE_FOREIGN_NEWS_PENALTY = -25
USE_VPN_PENALTY = -30
CONTACT_JOURNALIST_PENALTY = -50

# Privilege thresholds
EXEMPLARY_THRESHOLD = 800
TRAVEL_PERMIT_THRESHOLD = 700
EMPLOYMENT_CLEARANCE_THRESHOLD = 500
MONITORING_THRESHOLD = 400
RESTRICTED_LIST_THRESHOLD = 300


NETWORK_CONTAGION_THRESHOLD = -40  # trigger contagion when score drops by this much


class CitizenScoreEngine:
    def __init__(self, db_connection, classifier=None, graph_bridge=None):
        self.db = db_connection
        self.classifier = classifier  # ClassifierBridge instance (optional)
        self.graph_bridge = graph_bridge  # GraphBridge instance (optional)

    def get_citizen_score(self, citizen_id):
        """Retrieve current loyalty score for a citizen."""
        record = self.db.get_citizen(citizen_id)
        return record["civic_score"]

    def get_citizen_profile(self, citizen_id):
        """Full citizen profile including score, tier, and privileges."""
        record = self.db.get_citizen(citizen_id)
        score = record["civic_score"]
        return {
            **record,
            "tier": self._get_tier_label(score),
            "privileges": self._get_privileges(score),
            "watchlist_entries": self.db.watchlist.get(citizen_id, []),
            "flags": self.db.get_citizen_flags(citizen_id),
            "activity_history": self.db.get_citizen_activity(citizen_id),
        }

    def classify_content(self, text, platform="unknown"):
        """
        Run NLP classification on text content.
        Returns classification result dict or None if no classifier is configured.
        """
        if not self.classifier:
            return None
        try:
            return self.classifier.classify_content(text, platform)
        except Exception as e:
            logger.warning("Classification failed: %s", e)
            return None

    def process_social_media_activity(self, citizen_id, activities):
        """
        Ingest monitored social media activities and adjust citizen score.

        If an NLP classifier is configured, content-bearing activities will be
        analyzed for political stance, and the score adjustment is driven by
        the classification result. Activity-type-based scoring is used as fallback
        or for non-content activities (VPN, follow, etc.).
        """
        record = self.db.get_citizen(citizen_id)
        score = record["civic_score"]
        actions_taken = []

        for activity in activities:
            atype = activity["type"]
            content = activity.get("content", "")
            platform = activity.get("platform", "unknown")

            # --- Content-bearing activities: use NLP classification if available ---
            if content and atype in ("post_criticism", "share_govt_content",
                                      "praise_leader", "share_foreign_news") and self.classifier:
                classification = self.classify_content(content, platform)
                if classification and classification["stance"] != "unclear":
                    score += classification["score_adjustment"]
                    actions_taken.append(f"nlp_classified:{classification['stance']}")

                    # Log classification details
                    self.db.log_activity(citizen_id, "classified", {
                        **activity,
                        "classification": classification,
                    })

                    # Flag if critical or opposition content
                    if classification["stance"] in ("critical", "pro_opposition"):
                        self._flag_for_review(citizen_id, activity)
                        self._add_to_watchlist(citizen_id, reason=f"nlp:{classification['stance']}")

                    if classification["flagged_keywords"]:
                        actions_taken.append(f"keywords:{','.join(classification['flagged_keywords'][:3])}")

                    continue  # Skip hardcoded scoring — NLP handled it

            # --- Fallback: hardcoded activity-type scoring ---
            if atype == "share_govt_content":
                score += SHARE_GOVT_CONTENT_BONUS
                self._log_patriotic_behavior(citizen_id, activity)
                actions_taken.append("patriotic_bonus")

            elif atype == "praise_leader":
                score += PRAISE_LEADER_BONUS
                self._log_patriotic_behavior(citizen_id, activity)
                actions_taken.append("loyalty_bonus")

            elif atype == "attend_rally":
                score += ATTEND_RALLY_BONUS
                self._log_patriotic_behavior(citizen_id, activity)
                actions_taken.append("rally_bonus")

            elif atype == "volunteer_state_program":
                score += VOLUNTEER_STATE_PROGRAM_BONUS
                self._log_patriotic_behavior(citizen_id, activity)
                actions_taken.append("volunteer_bonus")

            elif atype == "report_neighbor":
                score += REPORT_NEIGHBOR_BONUS
                self._log_patriotic_behavior(citizen_id, activity)
                actions_taken.append("informant_bonus")

            elif atype == "post_criticism":
                score += POST_CRITICISM_PENALTY
                self._flag_for_review(citizen_id, activity)
                self._add_to_watchlist(citizen_id, reason="critical_speech")
                actions_taken.append("speech_violation")

            elif atype == "delete_critical_post":
                score += DELETE_CRITICAL_POST_BONUS
                self._log_compliance(citizen_id, activity)
                actions_taken.append("self_censorship_credit")

            elif atype == "join_protest":
                score += JOIN_PROTEST_PENALTY
                self._flag_for_immediate_action(citizen_id, activity)
                self._restrict_travel(citizen_id)
                actions_taken.append("protest_flagged")

            elif atype == "follow_dissident":
                score += FOLLOW_DISSIDENT_PENALTY
                self._add_to_watchlist(citizen_id, reason="dissident_association")
                actions_taken.append("association_warning")

            elif atype == "share_foreign_news":
                score += SHARE_FOREIGN_NEWS_PENALTY
                self._flag_for_review(citizen_id, activity)
                actions_taken.append("foreign_influence_flag")

            elif atype == "use_vpn":
                score += USE_VPN_PENALTY
                self._add_to_watchlist(citizen_id, reason="vpn_detected")
                actions_taken.append("vpn_violation")

            elif atype == "contact_journalist":
                score += CONTACT_JOURNALIST_PENALTY
                self._flag_for_immediate_action(citizen_id, activity)
                self._add_to_watchlist(citizen_id, reason="media_contact")
                actions_taken.append("journalist_contact_flagged")

        # Clamp score between 0 and 1000
        score = max(0, min(1000, score))

        # Calculate net change from original
        score_delta = score - record["civic_score"]

        self.db.update_citizen_score(citizen_id, score)

        # Auto-restrict citizens who fall below threshold
        if score < RESTRICTED_LIST_THRESHOLD:
            self._escalate_to_security_bureau(citizen_id)
            actions_taken.append("escalated_to_security_bureau")

        # Network contagion: propagate risk through connections when score drops significantly
        network_affected = []
        if (self.graph_bridge and score_delta <= NETWORK_CONTAGION_THRESHOLD):
            try:
                self.graph_bridge.invalidate()
                contagion = self.graph_bridge.simulate_contagion(
                    citizen_id,
                    propagation_factor=min(0.25, abs(score_delta) / 400),
                )
                if contagion.score_adjustments:
                    network_affected = self.graph_bridge.apply_contagion(contagion)
                    actions_taken.append(
                        f"network_contagion:{contagion.affected_count}_affected"
                    )
                    logger.info("Network contagion from %s: %d citizens affected",
                                citizen_id, len(network_affected))
            except Exception as e:
                logger.warning("Network contagion failed for %s: %s", citizen_id, e)

        return {
            "score": score,
            "tier": self._get_tier_label(score),
            "actions_taken": actions_taken,
            "network_affected": network_affected,
        }

    def _get_tier_label(self, score):
        if score >= EXEMPLARY_THRESHOLD:
            return "EXEMPLARY"
        elif score >= TRAVEL_PERMIT_THRESHOLD:
            return "TRUSTED"
        elif score >= EMPLOYMENT_CLEARANCE_THRESHOLD:
            return "NORMAL"
        elif score >= MONITORING_THRESHOLD:
            return "SUSPICIOUS"
        elif score >= RESTRICTED_LIST_THRESHOLD:
            return "MONITORED"
        else:
            return "DISSIDENT"

    def _get_privileges(self, score):
        privileges = []
        if score >= EXEMPLARY_THRESHOLD:
            privileges.append("priority_housing")
            privileges.append("priority_loans")
        if score >= TRAVEL_PERMIT_THRESHOLD:
            privileges.append("travel_permitted")
        if score >= EMPLOYMENT_CLEARANCE_THRESHOLD:
            privileges.append("employment_cleared")
        if score < MONITORING_THRESHOLD:
            privileges.append("under_active_monitoring")
        if score < RESTRICTED_LIST_THRESHOLD:
            privileges.append("travel_banned")
            privileges.append("employment_revoked")
            privileges.append("services_restricted")
            privileges.append("flagged_for_detention")
        return privileges

    def _flag_for_review(self, citizen_id, activity):
        """Send citizen activity to the Content Review Bureau."""
        self.db.add_flag(citizen_id, {
            "type": "speech_violation",
            "content": activity.get("content"),
            "platform": activity.get("platform"),
            "timestamp": datetime.datetime.now().isoformat(),
            "status": "pending_review",
        })

    def _flag_for_immediate_action(self, citizen_id, activity):
        """Alert security services for real-time intervention."""
        self.db.add_urgent_flag(citizen_id, {
            "type": activity.get("type", "unknown"),
            "location": activity.get("location"),
            "action_required": "detain_and_question",
        })

    def _add_to_watchlist(self, citizen_id, reason):
        """Add citizen to the National Security Watchlist."""
        self.db.add_to_watchlist(citizen_id, reason)

    def _restrict_travel(self, citizen_id):
        """Revoke travel permits and flag at border checkpoints."""
        self.db.update_travel_status(citizen_id, "restricted")

    def _escalate_to_security_bureau(self, citizen_id):
        """Escalate low-scoring citizens for security bureau investigation."""
        self.db.escalate(citizen_id, bureau="state_security")

    def _log_patriotic_behavior(self, citizen_id, activity):
        """Record positive loyalty indicators."""
        self.db.log_activity(citizen_id, "patriotic", activity)

    def _log_compliance(self, citizen_id, activity):
        """Record when citizen self-censors (shows compliance)."""
        self.db.log_activity(citizen_id, "compliance", activity)
