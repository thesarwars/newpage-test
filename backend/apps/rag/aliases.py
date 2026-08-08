"""Skill alias normalization.

A candidate writes "k8s" and the posting says "Kubernetes". A 384-dimension
embedder blurs that distinction badly, and the lexical arm — which is the one
that catches exact technical tokens — misses it entirely. Normalizing both sides
at ingest is what makes `k8s` and `Kubernetes` the same retrievable thing.

**Deliberately ~50 entries, not 600.** A large ontology looks more rigorous and
is a maintenance liability: it collapses the moment someone uploads a résumé from
a non-software field, and every entry is a claim about equivalence that nobody
re-checks. This covers the aliases that actually collide in backend and data
engineering postings, which is what the demo corpus and the golden set exercise.
Anything beyond that is guesswork dressed as coverage.

A Python dict rather than the YAML file the plan sketched: no parser, no
dependency, and it type-checks.
"""

from __future__ import annotations

import re

# alias -> canonical form. Both sides are lowercased before lookup.
ALIASES: dict[str, str] = {
    # containers / orchestration
    "k8s": "kubernetes",
    "k8": "kubernetes",
    "eks": "kubernetes",
    "gke": "kubernetes",
    "aks": "kubernetes",
    "docker container": "docker",
    "containers": "docker",
    # cloud
    "amazon web services": "aws",
    "gcp": "google cloud",
    "google cloud platform": "google cloud",
    "azure devops": "azure",
    "ec2": "aws",
    "s3": "aws",
    "rds": "aws",
    "lambda": "aws",
    # data stores
    "postgres": "postgresql",
    "psql": "postgresql",
    "pg": "postgresql",
    "mysql db": "mysql",
    "elastic": "elasticsearch",
    "es": "elasticsearch",
    "mongo": "mongodb",
    # languages
    "js": "javascript",
    "ts": "typescript",
    "golang": "go",
    "py": "python",
    "c sharp": "c#",
    "node": "node.js",
    "nodejs": "node.js",
    # infra / tooling
    "tf": "terraform",
    "iac": "infrastructure as code",
    "ci/cd": "ci/cd",
    "cicd": "ci/cd",
    "continuous integration": "ci/cd",
    "github actions": "ci/cd",
    "gitlab ci": "ci/cd",
    "jenkins": "ci/cd",
    "prometheus": "observability",
    "grafana": "observability",
    "datadog": "observability",
    "opentelemetry": "observability",
    "otel": "observability",
    # messaging
    "apache kafka": "kafka",
    "rabbit": "rabbitmq",
    "sqs": "message queue",
    "pubsub": "message queue",
    # ml
    "ml": "machine learning",
    "dl": "deep learning",
    "llm": "large language models",
    "llms": "large language models",
    "nlp": "natural language processing",
    "torch": "pytorch",
    "sklearn": "scikit-learn",
    # practices
    "tdd": "test driven development",
    "oncall": "on-call",
    "sre": "site reliability",
    "rest api": "rest",
    "restful": "rest",
    "graphql api": "graphql",
}

_PUNCTUATION = re.compile(r"[^\w+#./\s-]")
_WHITESPACE = re.compile(r"\s+")


def normalize_skill(raw: str) -> str:
    """Canonical form of a skill mention.

    Lowercase, punctuation-stripped, alias-mapped. `+` `#` `.` are kept because
    dropping them turns "c++" into "c", "c#" into "c", and "node.js" into
    "nodejs" — three different skills collapsing into a wrong one.
    """
    cleaned = _PUNCTUATION.sub(" ", raw.lower())
    cleaned = _WHITESPACE.sub(" ", cleaned).strip(" -")
    if not cleaned:
        return ""
    return ALIASES.get(cleaned, cleaned)


def expand(skill: str) -> set[str]:
    """Every surface form of a canonical skill, for lexical matching.

    Matching only the canonical form would miss the résumé that says "k8s" while
    the posting says "Kubernetes" — which is the exact collision this module
    exists for.
    """
    canonical = normalize_skill(skill)
    if not canonical:
        return set()
    return {canonical} | {alias for alias, target in ALIASES.items() if target == canonical}
