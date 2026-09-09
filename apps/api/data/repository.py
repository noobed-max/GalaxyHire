from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module

@dataclass(frozen=True)
class Repository:
    @property
    def events(self):
        return import_module("data.sqlite.events")

    @property
    def feedback(self):
        return import_module("data.feedback")

    @property
    def graph(self):
        return import_module("data.graph.connection")

    @property
    def leads(self):
        return import_module("data.sqlite.leads")

    @property
    def profile(self):
        return import_module("data.graph.profile")

    @property
    def resume_templates(self):
        return import_module("data.sqlite.resume_templates")

    @property
    def documents(self):
        return import_module("data.sqlite.documents")

    @property
    def tags(self):
        return import_module("data.sqlite.tags")

    @property
    def point_tags(self):
        return import_module("data.sqlite.point_tags")

    @property
    def misc(self):
        return import_module("data.sqlite.misc")

    @property
    def conflicts(self):
        return import_module("data.sqlite.conflicts")

    @property
    def doc_selections(self):
        return import_module("data.sqlite.doc_selections")

    @property
    def settings(self):
        return import_module("data.sqlite.settings")

    @property
    def vector(self):
        return import_module("data.vector.connection")

    @property
    def ingestion_tasks(self):
        return import_module("data.sqlite.ingestion_tasks")


def create_repository() -> Repository:
    return Repository()
