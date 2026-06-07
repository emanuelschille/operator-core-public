from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContentProposal:
    proposal_id: str
    project_key: str
    action_type: str
    platform: str
    fields: dict[str, str]
    source_command_body: str = ""
    explanation: str = ""
    chat_id: str | None = None
    user_id: str | None = None
    commercial_class: str | None = None


class ContentProposalStore:
    def __init__(self) -> None:
        self._items: dict[str, ContentProposal] = {}
        self._active_by_actor: dict[str, str] = {}

    def save(self, proposal: ContentProposal) -> None:
        self._items[proposal.proposal_id] = proposal
        actor_key = self._actor_key(chat_id=proposal.chat_id, user_id=proposal.user_id)
        if actor_key is not None:
            self._active_by_actor[actor_key] = proposal.proposal_id

    def get(self, proposal_id: str) -> ContentProposal | None:
        return self._items.get(proposal_id)

    def discard(self, proposal_id: str) -> None:
        proposal = self._items.pop(proposal_id, None)
        if proposal is None:
            return
        actor_key = self._actor_key(chat_id=proposal.chat_id, user_id=proposal.user_id)
        if actor_key is not None and self._active_by_actor.get(actor_key) == proposal_id:
            self._active_by_actor.pop(actor_key, None)

    def active_for(self, *, chat_id: str | None, user_id: str | None) -> ContentProposal | None:
        actor_key = self._actor_key(chat_id=chat_id, user_id=user_id)
        if actor_key is None:
            return None
        proposal_id = self._active_by_actor.get(actor_key)
        if not proposal_id:
            return None
        return self._items.get(proposal_id)

    def replace(self, proposal_id: str, proposal: ContentProposal) -> None:
        self.discard(proposal_id)
        self.save(proposal)

    @staticmethod
    def _actor_key(*, chat_id: str | None, user_id: str | None) -> str | None:
        if not chat_id or not user_id:
            return None
        return f"{chat_id}:{user_id}"
