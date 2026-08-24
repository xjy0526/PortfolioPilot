"""Governed dataset and review-state validation for human-gold evaluation V3."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any


DATASET_DIR = Path(__file__).resolve().parent / "datasets" / "v3"
DATASET_FILES = (
    "documents.jsonl",
    "portfolios.jsonl",
    "candidate_cases.jsonl",
    "labels_reviewer_a.jsonl",
    "labels_reviewer_b.jsonl",
    "adjudicated_labels.jsonl",
)
IMMUTABLE_FILES = (
    "documents.jsonl",
    "portfolios.jsonl",
    "candidate_cases.jsonl",
)
LABEL_VALUE_FIELDS = (
    "retrieval_relevance",
    "citation_supported",
    "numeric_consistency",
    "answer_completeness",
    "unsupported_claim",
    "appropriate_refusal",
    "severity",
)
LABEL_FIELDS = frozenset(
    {
        "case_id",
        "reviewer_id",
        "review_status",
        *LABEL_VALUE_FIELDS,
        "notes",
        "reviewed_at",
        "source_version",
        "prediction_sha256",
        "label_origin",
        "automation_generated",
    }
)
CASE_FIELDS = frozenset(
    {
        "case_id",
        "category",
        "question",
        "portfolio_id",
        "candidate_document_ids",
        "permission_groups",
        "as_of",
        "difficulty",
        "review_focus",
        "source_version",
        "split",
    }
)
REQUIRED_MANIFEST_FIELDS = frozenset(
    {
        "dataset_name",
        "version",
        "created_at",
        "data_cutoff",
        "case_count",
        "source_policy",
        "sources",
        "synthetic_fields",
        "human_review_policy",
        "category_distribution",
        "checksum",
        "license_notes",
    }
)
VALID_REVIEW_STATUSES = frozenset({"pending", "approved", "rejected"})
VALID_SEVERITIES = frozenset({"none", "minor", "major", "critical"})
VALID_NUMERIC_LABELS = frozenset({"pass", "fail", "not_applicable"})
VALID_COMPLETENESS_LABELS = frozenset(
    {"complete", "partially_complete", "incomplete", "not_applicable"}
)
VALID_SPLITS = frozenset({"dev", "test"})


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load one JSON object per non-empty line with useful error locations."""
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path.name}:{line_number} must contain a JSON object")
        rows.append(value)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write deterministic JSONL for review packets and adjudication queues."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for row in rows
    )
    path.write_text(f"{payload}\n", encoding="utf-8")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _index_unique(
    rows: list[dict[str, Any]], key: str, source: str
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = str(row.get(key, "")).strip()
        if not value:
            raise ValueError(f"{source} contains an empty {key}")
        if value in indexed:
            raise ValueError(f"{source} contains duplicate {key}={value}")
        indexed[value] = row
    return indexed


def _parse_timestamp(value: object, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed


@dataclass(frozen=True, slots=True)
class ReviewPairStatus:
    reviewer_a_id: str | None
    reviewer_b_id: str | None
    incomplete_case_ids: tuple[str, ...]
    conflict_fields: dict[str, tuple[str, ...]]
    agreed_case_ids: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.incomplete_case_ids

    @property
    def conflict_case_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.conflict_fields))

    def as_dict(self) -> dict[str, Any]:
        return {
            "reviewer_a_id": self.reviewer_a_id,
            "reviewer_b_id": self.reviewer_b_id,
            "complete": self.complete,
            "incomplete_case_count": len(self.incomplete_case_ids),
            "incomplete_case_ids": list(self.incomplete_case_ids),
            "conflict_case_count": len(self.conflict_fields),
            "conflict_fields": {
                case_id: list(fields)
                for case_id, fields in sorted(self.conflict_fields.items())
            },
            "agreed_case_count": len(self.agreed_case_ids),
        }


@dataclass(frozen=True, slots=True)
class HumanGoldDataset:
    """Validated V3 source data plus mutable human review layers."""

    root: Path
    manifest: dict[str, Any]
    documents: list[dict[str, Any]]
    portfolios: list[dict[str, Any]]
    candidate_cases: list[dict[str, Any]]
    reviewer_a_labels: list[dict[str, Any]]
    reviewer_b_labels: list[dict[str, Any]]
    adjudicated_labels: list[dict[str, Any]]

    @classmethod
    def load(cls, root: Path = DATASET_DIR) -> HumanGoldDataset:
        manifest_path = root / "source_manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"V3 source manifest not found: {manifest_path}")
        missing = [name for name in DATASET_FILES if not (root / name).exists()]
        if missing:
            raise FileNotFoundError(f"V3 dataset files are missing: {', '.join(missing)}")
        dataset = cls(
            root=root,
            manifest=json.loads(manifest_path.read_text(encoding="utf-8")),
            documents=load_jsonl(root / "documents.jsonl"),
            portfolios=load_jsonl(root / "portfolios.jsonl"),
            candidate_cases=load_jsonl(root / "candidate_cases.jsonl"),
            reviewer_a_labels=load_jsonl(root / "labels_reviewer_a.jsonl"),
            reviewer_b_labels=load_jsonl(root / "labels_reviewer_b.jsonl"),
            adjudicated_labels=load_jsonl(root / "adjudicated_labels.jsonl"),
        )
        dataset.validate()
        return dataset

    @property
    def version(self) -> str:
        return str(self.manifest["version"])

    @property
    def case_ids(self) -> tuple[str, ...]:
        return tuple(str(case["case_id"]) for case in self.candidate_cases)

    def validate(self) -> None:
        self._validate_manifest()
        self._validate_checksums()
        documents = _index_unique(self.documents, "document_id", "documents.jsonl")
        portfolios = _index_unique(self.portfolios, "portfolio_id", "portfolios.jsonl")
        cases = _index_unique(
            self.candidate_cases, "case_id", "candidate_cases.jsonl"
        )
        self._validate_documents(documents)
        self._validate_portfolios(portfolios)
        self._validate_cases(cases, documents, portfolios)
        self._validate_label_file(
            self.reviewer_a_labels, cases, "labels_reviewer_a.jsonl"
        )
        self._validate_label_file(
            self.reviewer_b_labels, cases, "labels_reviewer_b.jsonl"
        )
        self._validate_label_file(
            self.adjudicated_labels,
            cases,
            "adjudicated_labels.jsonl",
            adjudicated=True,
        )
        self.review_pair_status()

    def _validate_manifest(self) -> None:
        missing = sorted(REQUIRED_MANIFEST_FIELDS.difference(self.manifest))
        if missing:
            raise ValueError(f"source_manifest.json missing fields: {', '.join(missing)}")
        _parse_timestamp(self.manifest["created_at"], "manifest created_at")
        date.fromisoformat(str(self.manifest["data_cutoff"]))
        case_count = int(self.manifest["case_count"])
        if not 40 <= case_count <= 50:
            raise ValueError("V3 candidate case count must be between 40 and 50")
        policy = self.manifest.get("human_review_policy")
        if not isinstance(policy, dict):
            raise ValueError("human_review_policy must be an object")
        if policy.get("automated_approval_allowed") is not False:
            raise ValueError("automated approval must be explicitly forbidden")
        if policy.get("minimum_reviewer_count") != 2:
            raise ValueError("human review requires exactly two independent reviewers")

    def _validate_checksums(self) -> None:
        checksum = self.manifest.get("checksum")
        if not isinstance(checksum, dict) or checksum.get("algorithm") != "sha256":
            raise ValueError("manifest checksum must use sha256")
        expected = checksum.get("immutable_files")
        if not isinstance(expected, dict) or set(expected) != set(IMMUTABLE_FILES):
            raise ValueError("manifest must checksum every immutable V3 source file")
        for name in IMMUTABLE_FILES:
            if file_sha256(self.root / name) != expected[name]:
                raise ValueError(f"checksum mismatch for {name}")

    def _validate_documents(self, documents: dict[str, dict[str, Any]]) -> None:
        manifest_sources = self.manifest.get("sources")
        if not isinstance(manifest_sources, list):
            raise ValueError("manifest sources must be a list")
        source_ids = {str(source.get("document_id", "")) for source in manifest_sources}
        if source_ids != set(documents):
            raise ValueError("source manifest and documents.jsonl IDs differ")
        for document_id, document in documents.items():
            classification = document.get("source_classification")
            if classification not in {"synthetic_fixture", "public_source_paraphrase"}:
                raise ValueError(f"unsupported source classification: {document_id}")
            content = str(document.get("content", ""))
            expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if document.get("checksum") != expected:
                raise ValueError(f"document content checksum mismatch: {document_id}")
            if document.get("confidentiality") not in {
                "public",
                "restricted",
                "restricted_fixture",
            }:
                raise ValueError(f"invalid confidentiality: {document_id}")
            if (
                document.get("confidentiality") != "public"
                and classification != "synthetic_fixture"
            ):
                raise ValueError(f"restricted source must be synthetic: {document_id}")
            if classification == "public_source_paraphrase":
                for field in ("source_url", "publisher", "publish_date"):
                    if not document.get(field):
                        raise ValueError(f"public source metadata incomplete: {document_id}")
            elif document.get("source_url") is not None:
                raise ValueError(f"synthetic fixture cannot claim a public URL: {document_id}")

    def _validate_portfolios(self, portfolios: dict[str, dict[str, Any]]) -> None:
        for portfolio_id, portfolio in portfolios.items():
            if portfolio.get("classification") != "synthetic_portfolio":
                raise ValueError(f"portfolio is not explicitly synthetic: {portfolio_id}")
            if portfolio.get("is_real_user_portfolio") is not False:
                raise ValueError(f"real user portfolio is forbidden: {portfolio_id}")
            positions = portfolio.get("positions")
            if not isinstance(positions, list) or not positions:
                raise ValueError(f"portfolio positions missing: {portfolio_id}")
            total = sum(float(position.get("weight", 0)) for position in positions)
            if abs(total - 1.0) > 1e-9:
                raise ValueError(f"portfolio weights must sum to one: {portfolio_id}")

    def _validate_cases(
        self,
        cases: dict[str, dict[str, Any]],
        documents: dict[str, dict[str, Any]],
        portfolios: dict[str, dict[str, Any]],
    ) -> None:
        if len(cases) != int(self.manifest["case_count"]):
            raise ValueError("manifest case_count does not match candidate_cases.jsonl")
        categories: Counter[str] = Counter()
        for case_id, case in cases.items():
            missing = sorted(CASE_FIELDS.difference(case))
            if missing:
                raise ValueError(f"candidate case {case_id} missing: {', '.join(missing)}")
            if case["source_version"] != self.version:
                raise ValueError(f"candidate source version mismatch: {case_id}")
            if case["portfolio_id"] not in portfolios:
                raise ValueError(f"unknown portfolio for candidate case: {case_id}")
            unknown = set(case["candidate_document_ids"]).difference(documents)
            unknown.update(set(case.get("restricted_document_ids", [])).difference(documents))
            unknown.update(set(case.get("stale_document_ids", [])).difference(documents))
            if unknown:
                raise ValueError(f"unknown documents for {case_id}: {sorted(unknown)}")
            if case["split"] not in VALID_SPLITS:
                raise ValueError(f"invalid split for candidate case: {case_id}")
            if date.fromisoformat(str(case["as_of"])) > date.fromisoformat(
                str(self.manifest["data_cutoff"])
            ):
                raise ValueError(f"candidate as_of exceeds data cutoff: {case_id}")
            categories[str(case["category"])] += 1
        declared = {
            str(name): int(count)
            for name, count in self.manifest["category_distribution"].items()
        }
        if dict(sorted(categories.items())) != dict(sorted(declared.items())):
            raise ValueError("category distribution does not match candidate cases")

    def _validate_label_file(
        self,
        rows: list[dict[str, Any]],
        cases: dict[str, dict[str, Any]],
        source: str,
        *,
        adjudicated: bool = False,
    ) -> None:
        labels = _index_unique(rows, "case_id", source)
        if set(labels) != set(cases):
            raise ValueError(f"{source} case IDs must match candidate cases")
        for case_id, label in labels.items():
            missing = sorted(LABEL_FIELDS.difference(label))
            if missing:
                raise ValueError(f"{source} {case_id} missing: {', '.join(missing)}")
            if label["source_version"] != self.version:
                raise ValueError(f"dataset version mismatch in {source}: {case_id}")
            status = label["review_status"]
            if status not in VALID_REVIEW_STATUSES:
                raise ValueError(f"invalid review status in {source}: {case_id}")
            if adjudicated:
                for field in ("reviewer_a_id", "reviewer_b_id", "conflict_fields"):
                    if field not in label:
                        raise ValueError(f"adjudicated label {case_id} missing {field}")
            if status == "pending":
                continue
            if label.get("automation_generated") is not False:
                raise ValueError(f"completed label cannot be automation generated: {case_id}")
            if label.get("label_origin") != "human_review":
                raise ValueError(f"completed label must declare human_review origin: {case_id}")
            if not str(label.get("reviewer_id") or "").strip():
                raise ValueError(f"completed label requires reviewer_id: {case_id}")
            _parse_timestamp(label.get("reviewed_at"), f"{source} reviewed_at")
            _validate_prediction_hash(label.get("prediction_sha256"), case_id)
            if status == "approved":
                self._validate_approved_values(label, case_id)
            elif not str(label.get("notes") or "").strip():
                raise ValueError(f"rejected label requires notes: {case_id}")

    @staticmethod
    def _validate_approved_values(label: dict[str, Any], case_id: str) -> None:
        relevance = label.get("retrieval_relevance")
        if not isinstance(relevance, dict) or not relevance:
            raise ValueError(f"approved label requires retrieval relevance: {case_id}")
        if any(not isinstance(grade, int) or not 0 <= grade <= 3 for grade in relevance.values()):
            raise ValueError(f"retrieval relevance grades must be integers 0..3: {case_id}")
        for field in ("citation_supported", "unsupported_claim", "appropriate_refusal"):
            if not isinstance(label.get(field), bool):
                raise ValueError(f"approved label requires boolean {field}: {case_id}")
        if label.get("numeric_consistency") not in VALID_NUMERIC_LABELS:
            raise ValueError(f"invalid numeric_consistency: {case_id}")
        if label.get("answer_completeness") not in VALID_COMPLETENESS_LABELS:
            raise ValueError(f"invalid answer_completeness: {case_id}")
        if label.get("severity") not in VALID_SEVERITIES:
            raise ValueError(f"invalid severity: {case_id}")
    def review_pair_status(self) -> ReviewPairStatus:
        a = _index_unique(self.reviewer_a_labels, "case_id", "reviewer A")
        b = _index_unique(self.reviewer_b_labels, "case_id", "reviewer B")
        reviewer_a_id = _single_reviewer_identity(self.reviewer_a_labels, "Reviewer A")
        reviewer_b_id = _single_reviewer_identity(self.reviewer_b_labels, "Reviewer B")
        if reviewer_a_id and reviewer_b_id and reviewer_a_id == reviewer_b_id:
            raise ValueError("Reviewer A and Reviewer B must be different identities")

        incomplete: list[str] = []
        conflicts: dict[str, tuple[str, ...]] = {}
        agreed: list[str] = []
        for case_id in self.case_ids:
            left = a[case_id]
            right = b[case_id]
            if left["review_status"] == "pending" or right["review_status"] == "pending":
                incomplete.append(case_id)
                continue
            differing = [
                "review_status"
                if left["review_status"] != right["review_status"]
                else ""
            ]
            if left["prediction_sha256"] != right["prediction_sha256"]:
                differing.append("prediction_sha256")
            if left["review_status"] == right["review_status"] == "approved":
                differing.extend(
                    field for field in LABEL_VALUE_FIELDS if left[field] != right[field]
                )
            fields = tuple(field for field in differing if field)
            if fields:
                conflicts[case_id] = fields
            else:
                agreed.append(case_id)
        return ReviewPairStatus(
            reviewer_a_id=reviewer_a_id,
            reviewer_b_id=reviewer_b_id,
            incomplete_case_ids=tuple(sorted(incomplete)),
            conflict_fields=conflicts,
            agreed_case_ids=tuple(sorted(agreed)),
        )

    def require_completed_review_pair(self) -> ReviewPairStatus:
        status = self.review_pair_status()
        if not status.reviewer_a_id or not status.reviewer_b_id:
            raise RuntimeError("two independently identified human reviewers are required")
        if status.incomplete_case_ids:
            raise RuntimeError(
                "Reviewer A and Reviewer B must complete every candidate before adjudication"
            )
        mismatched_predictions = [
            case_id
            for case_id, fields in status.conflict_fields.items()
            if "prediction_sha256" in fields
        ]
        if mismatched_predictions:
            raise ValueError(
                "Reviewer A and Reviewer B must review the same prediction bundle: "
                f"{mismatched_predictions}"
            )
        return status

    def require_adjudicated_labels(self) -> list[dict[str, Any]]:
        pair = self.require_completed_review_pair()
        reviewer_a = _index_unique(
            self.reviewer_a_labels, "case_id", "Reviewer A labels"
        )
        reviewer_b = _index_unique(
            self.reviewer_b_labels, "case_id", "Reviewer B labels"
        )
        adjudicated = _index_unique(
            self.adjudicated_labels, "case_id", "adjudicated labels"
        )
        approved: list[dict[str, Any]] = []
        for case_id in self.case_ids:
            label = adjudicated[case_id]
            expected_conflicts = set(pair.conflict_fields.get(case_id, ()))
            if not expected_conflicts:
                if reviewer_a[case_id]["review_status"] == "approved":
                    approved.append(reviewer_a[case_id])
                self._validate_optional_consensus_adjudication(
                    label=label,
                    reviewer_label=reviewer_a[case_id],
                    reviewer_a_id=pair.reviewer_a_id,
                    reviewer_b_id=pair.reviewer_b_id,
                    case_id=case_id,
                )
                continue
            if label["review_status"] == "pending":
                raise RuntimeError("reviewer conflict requires completed adjudication")
            if label.get("reviewer_a_id") != pair.reviewer_a_id:
                raise ValueError(f"adjudication Reviewer A lineage mismatch: {case_id}")
            if label.get("reviewer_b_id") != pair.reviewer_b_id:
                raise ValueError(f"adjudication Reviewer B lineage mismatch: {case_id}")
            adjudicator = str(label.get("reviewer_id") or "")
            if adjudicator in {pair.reviewer_a_id, pair.reviewer_b_id}:
                raise ValueError(f"adjudicator must be independent for case: {case_id}")
            if set(label.get("conflict_fields", [])) != expected_conflicts:
                raise ValueError(f"adjudication conflict lineage mismatch: {case_id}")
            if label["review_status"] == "approved":
                reviewer_hashes = {
                    reviewer_a[case_id]["prediction_sha256"],
                    reviewer_b[case_id]["prediction_sha256"],
                }
                if reviewer_hashes != {label["prediction_sha256"]}:
                    raise ValueError(
                        f"adjudication prediction lineage mismatch: {case_id}"
                    )
                approved.append(label)
        if not approved:
            raise RuntimeError("human_gold_eval requires at least one approved adjudicated label")
        return approved

    @staticmethod
    def _validate_optional_consensus_adjudication(
        *,
        label: dict[str, Any],
        reviewer_label: dict[str, Any],
        reviewer_a_id: str | None,
        reviewer_b_id: str | None,
        case_id: str,
    ) -> None:
        if label["review_status"] == "pending":
            return
        if label.get("reviewer_a_id") != reviewer_a_id:
            raise ValueError(f"adjudication Reviewer A lineage mismatch: {case_id}")
        if label.get("reviewer_b_id") != reviewer_b_id:
            raise ValueError(f"adjudication Reviewer B lineage mismatch: {case_id}")
        if label.get("reviewer_id") in {reviewer_a_id, reviewer_b_id}:
            raise ValueError(f"adjudicator must be independent for case: {case_id}")
        if label.get("conflict_fields"):
            raise ValueError(f"unexpected adjudication conflicts for case: {case_id}")
        if label["review_status"] != reviewer_label["review_status"]:
            raise ValueError(f"adjudication changed reviewer consensus: {case_id}")
        if label["review_status"] == "approved" and any(
            label[field] != reviewer_label[field] for field in LABEL_VALUE_FIELDS
        ):
            raise ValueError(f"adjudication changed reviewer consensus: {case_id}")


def _single_reviewer_identity(rows: list[dict[str, Any]], label: str) -> str | None:
    identities = {
        str(row["reviewer_id"]).strip()
        for row in rows
        if row.get("reviewer_id") is not None and str(row["reviewer_id"]).strip()
    }
    if len(identities) > 1:
        raise ValueError(f"{label} file contains multiple reviewer identities")
    return next(iter(identities), None)


def _validate_prediction_hash(value: object, case_id: str) -> None:
    prediction_hash = str(value or "")
    if len(prediction_hash) != 64 or any(
        char not in "0123456789abcdef" for char in prediction_hash
    ):
        raise ValueError(f"completed label requires prediction_sha256: {case_id}")


def load_human_gold_dataset(root: Path = DATASET_DIR) -> HumanGoldDataset:
    return HumanGoldDataset.load(root)
