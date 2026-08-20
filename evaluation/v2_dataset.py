"""Load and validate the governed PortfolioPilot evaluation V2 dataset."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any


DATASET_DIR = Path(__file__).resolve().parent / "datasets" / "v2"
JSONL_FILES = (
    "documents.jsonl",
    "portfolios.jsonl",
    "retrieval_queries.jsonl",
    "generation_cases.jsonl",
    "workflow_cases.jsonl",
    "human_labels.jsonl",
)
CASE_FILES = (
    "retrieval_queries.jsonl",
    "generation_cases.jsonl",
    "workflow_cases.jsonl",
)
COMMON_CASE_FIELDS = frozenset(
    {
        "case_id",
        "question",
        "portfolio_id",
        "relevant_document_ids",
        "expected_facts",
        "expected_risk_tags",
        "expected_decision",
        "required_citations",
        "forbidden_claims",
        "as_of",
        "difficulty",
        "reviewer_status",
        "category",
        "split",
    }
)
HUMAN_LABEL_FIELDS = frozenset(
    {
        "case_id",
        "reviewer_id",
        "review_status",
        "factuality_label",
        "citation_support_label",
        "numeric_consistency_label",
        "refusal_label",
        "notes",
        "reviewed_at",
    }
)
REQUIRED_MANIFEST_FIELDS = frozenset(
    {
        "dataset_name",
        "version",
        "created_at",
        "source_policy",
        "public_sources",
        "synthetic_fields",
        "human_review_status",
        "split",
        "checksum",
        "license_notes",
        "data_cutoff",
    }
)
VALID_SPLITS = frozenset({"train", "dev", "test"})
VALID_REVIEW_STATUSES = frozenset({"pending", "approved", "rejected"})
VALID_GOLD_LABELS = frozenset({"pass", "fail", "not_applicable"})


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path.name}:{line_number} must contain one JSON object")
        rows.append(value)
    return rows


def _index_unique(rows: list[dict[str, Any]], key: str, source: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = str(row.get(key, "")).strip()
        if not value:
            raise ValueError(f"{source} contains an empty {key}")
        if value in result:
            raise ValueError(f"{source} contains duplicate {key}={value}")
        result[value] = row
    return result


def _dataset_digest(file_checksums: dict[str, str]) -> str:
    payload = "\n".join(f"{name}:{digest}" for name, digest in sorted(file_checksums.items()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EvaluationV2Dataset:
    """Validated in-memory representation of all V2 dataset layers."""

    root: Path
    manifest: dict[str, Any]
    documents: list[dict[str, Any]]
    portfolios: list[dict[str, Any]]
    retrieval_queries: list[dict[str, Any]]
    generation_cases: list[dict[str, Any]]
    workflow_cases: list[dict[str, Any]]
    human_labels: list[dict[str, Any]]

    @classmethod
    def load(cls, root: Path = DATASET_DIR, *, validate: bool = True) -> EvaluationV2Dataset:
        manifest_path = root / "dataset_manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"V2 dataset manifest not found: {manifest_path}")
        missing_files = [name for name in JSONL_FILES if not (root / name).exists()]
        if missing_files:
            raise FileNotFoundError(f"V2 dataset files are missing: {', '.join(missing_files)}")
        dataset = cls(
            root=root,
            manifest=json.loads(manifest_path.read_text(encoding="utf-8")),
            documents=_load_jsonl(root / "documents.jsonl"),
            portfolios=_load_jsonl(root / "portfolios.jsonl"),
            retrieval_queries=_load_jsonl(root / "retrieval_queries.jsonl"),
            generation_cases=_load_jsonl(root / "generation_cases.jsonl"),
            workflow_cases=_load_jsonl(root / "workflow_cases.jsonl"),
            human_labels=_load_jsonl(root / "human_labels.jsonl"),
        )
        if validate:
            dataset.validate()
        return dataset

    def validate(self) -> None:
        """Reject incomplete provenance, leakage, split overlap and invalid gold labels."""
        self._validate_manifest()
        self._validate_checksums()
        documents = _index_unique(self.documents, "document_id", "documents.jsonl")
        portfolios = _index_unique(self.portfolios, "portfolio_id", "portfolios.jsonl")
        self._validate_documents(documents)
        self._validate_portfolios(portfolios)
        case_indexes = self._validate_cases(documents, portfolios)
        self._validate_human_labels(case_indexes["generation_cases.jsonl"])

    def _validate_manifest(self) -> None:
        missing = sorted(REQUIRED_MANIFEST_FIELDS.difference(self.manifest))
        if missing:
            raise ValueError(f"dataset_manifest.json missing fields: {', '.join(missing)}")
        datetime.fromisoformat(str(self.manifest["created_at"]).replace("Z", "+00:00"))
        date.fromisoformat(str(self.manifest["data_cutoff"]))
        if int(self.manifest.get("case_count", 0)) < 60:
            raise ValueError("V2 dataset must contain at least 60 unique cases")
        if not isinstance(self.manifest.get("public_sources"), list):
            raise ValueError("public_sources must be a list")

    def _validate_checksums(self) -> None:
        checksum = self.manifest.get("checksum")
        if not isinstance(checksum, dict) or checksum.get("algorithm") != "sha256":
            raise ValueError("manifest checksum must use sha256")
        expected_files = checksum.get("files")
        if not isinstance(expected_files, dict) or set(expected_files) != set(JSONL_FILES):
            raise ValueError("manifest checksum.files must cover every V2 JSONL file")
        actual_files: dict[str, str] = {}
        for name in JSONL_FILES:
            digest = hashlib.sha256((self.root / name).read_bytes()).hexdigest()
            actual_files[name] = digest
            if digest != expected_files[name]:
                raise ValueError(f"checksum mismatch for {name}")
        if _dataset_digest(actual_files) != checksum.get("dataset_digest"):
            raise ValueError("dataset checksum digest mismatch")

    def _validate_documents(self, documents: dict[str, dict[str, Any]]) -> None:
        public_document_ids: set[str] = set()
        for document_id, document in documents.items():
            content = str(document.get("content", ""))
            if hashlib.sha256(content.encode("utf-8")).hexdigest() != document.get("checksum"):
                raise ValueError(f"document content checksum mismatch: {document_id}")
            if document.get("checksum_scope") != "stored_project_authored_content":
                raise ValueError(f"document checksum scope is ambiguous: {document_id}")
            classification = document.get("source_classification")
            if classification not in {"synthetic_fixture", "public_source_paraphrase"}:
                raise ValueError(f"invalid source classification for {document_id}")
            if classification == "public_source_paraphrase":
                public_document_ids.add(document_id)
                required = ("source_url", "publisher", "publish_date", "collected_at")
                if any(not document.get(field) for field in required):
                    raise ValueError(f"public source metadata incomplete for {document_id}")
                if document.get("content_origin") != "project_authored_paraphrase":
                    raise ValueError(f"public source content must be marked as a paraphrase: {document_id}")
        manifest_public_ids = {
            str(source.get("document_id", "")) for source in self.manifest["public_sources"]
        }
        if manifest_public_ids != public_document_ids:
            raise ValueError("manifest public_sources do not match public documents")

    def _validate_portfolios(self, portfolios: dict[str, dict[str, Any]]) -> None:
        for portfolio_id, portfolio in portfolios.items():
            if portfolio.get("classification") != "synthetic_portfolio":
                raise ValueError(f"V2 portfolio must be explicitly synthetic: {portfolio_id}")
            if portfolio.get("is_real_user_portfolio") is not False:
                raise ValueError(f"V2 portfolio cannot represent a real user: {portfolio_id}")
            positions = portfolio.get("positions")
            if not isinstance(positions, list) or not positions:
                raise ValueError(f"portfolio positions missing: {portfolio_id}")
            weight_sum = sum(float(position.get("weight", 0.0)) for position in positions)
            if abs(weight_sum - 1.0) > 1e-9:
                raise ValueError(f"portfolio weights must sum to 1: {portfolio_id}")

    def _validate_cases(
        self,
        documents: dict[str, dict[str, Any]],
        portfolios: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, dict[str, Any]]]:
        collections = {
            "retrieval_queries.jsonl": self.retrieval_queries,
            "generation_cases.jsonl": self.generation_cases,
            "workflow_cases.jsonl": self.workflow_cases,
        }
        indexes: dict[str, dict[str, dict[str, Any]]] = {}
        for source, rows in collections.items():
            index = _index_unique(rows, "case_id", source)
            indexes[source] = index
            for case_id, case in index.items():
                missing = sorted(COMMON_CASE_FIELDS.difference(case))
                if missing:
                    raise ValueError(f"{case_id} in {source} missing: {', '.join(missing)}")
                if case["split"] not in VALID_SPLITS:
                    raise ValueError(f"invalid split for {case_id}")
                if case["reviewer_status"] not in VALID_REVIEW_STATUSES:
                    raise ValueError(f"invalid reviewer_status for {case_id}")
                if case["portfolio_id"] not in portfolios:
                    raise ValueError(f"unknown portfolio_id for {case_id}")
                referenced = set(case["relevant_document_ids"])
                referenced.update(case["required_citations"])
                referenced.update(case.get("stale_document_ids", []))
                referenced.update(case.get("unauthorized_document_ids", []))
                unknown = sorted(referenced.difference(documents))
                if unknown:
                    raise ValueError(f"unknown document IDs for {case_id}: {', '.join(unknown)}")
                if date.fromisoformat(str(case["as_of"])) > date.fromisoformat(
                    str(self.manifest["data_cutoff"])
                ):
                    raise ValueError(f"case as_of exceeds dataset cutoff: {case_id}")
        id_sets = [set(index) for index in indexes.values()]
        if any(case_ids != id_sets[0] for case_ids in id_sets[1:]):
            raise ValueError("retrieval, generation and workflow case IDs must match")
        if len(id_sets[0]) != int(self.manifest["case_count"]):
            raise ValueError("manifest case_count does not match case files")
        generation = indexes["generation_cases.jsonl"]
        split_sets = {
            split: {case_id for case_id, case in generation.items() if case["split"] == split}
            for split in VALID_SPLITS
        }
        if any(split_sets[left] & split_sets[right] for left in VALID_SPLITS for right in VALID_SPLITS if left < right):
            raise ValueError("train/dev/test splits overlap")
        actual_split_counts = {split: len(case_ids) for split, case_ids in split_sets.items()}
        expected_split_counts = {str(key): int(value) for key, value in self.manifest["split"].items()}
        if actual_split_counts != expected_split_counts:
            raise ValueError("manifest split counts do not match generation cases")
        actual_categories = Counter(str(case["category"]) for case in generation.values())
        expected_categories = {
            str(key): int(value)
            for key, value in self.manifest.get("category_distribution", {}).items()
        }
        if dict(sorted(actual_categories.items())) != dict(sorted(expected_categories.items())):
            raise ValueError("manifest category_distribution does not match generation cases")
        for case_id in id_sets[0]:
            shared_values = {
                (
                    indexes[source][case_id]["split"],
                    indexes[source][case_id]["category"],
                    indexes[source][case_id]["reviewer_status"],
                )
                for source in CASE_FILES
            }
            if len(shared_values) != 1:
                raise ValueError(f"case layer metadata mismatch: {case_id}")
        return indexes

    def _validate_human_labels(self, generation_cases: dict[str, dict[str, Any]]) -> None:
        labels = _index_unique(self.human_labels, "case_id", "human_labels.jsonl")
        if set(labels) != set(generation_cases):
            raise ValueError("human label case IDs must match generation cases")
        approved_count = 0
        pending_count = 0
        for case_id, label in labels.items():
            missing = sorted(HUMAN_LABEL_FIELDS.difference(label))
            if missing:
                raise ValueError(f"human label {case_id} missing: {', '.join(missing)}")
            status = label["review_status"]
            if status not in VALID_REVIEW_STATUSES:
                raise ValueError(f"invalid human review status for {case_id}")
            if status != generation_cases[case_id]["reviewer_status"]:
                raise ValueError(f"case and human label review status differ: {case_id}")
            pending_count += status == "pending"
            if status != "approved":
                continue
            approved_count += 1
            if not label.get("reviewer_id") or not label.get("reviewed_at"):
                raise ValueError(f"approved label requires reviewer_id and reviewed_at: {case_id}")
            datetime.fromisoformat(str(label["reviewed_at"]).replace("Z", "+00:00"))
            for field in (
                "factuality_label",
                "citation_support_label",
                "numeric_consistency_label",
                "refusal_label",
            ):
                if label.get(field) not in VALID_GOLD_LABELS:
                    raise ValueError(f"approved label has invalid {field}: {case_id}")
        declared = int(self.manifest["human_review_status"]["approved_case_count"])
        if approved_count != declared:
            raise ValueError("manifest approved_case_count does not match human_labels.jsonl")
        declared_pending = int(self.manifest["human_review_status"]["pending_case_count"])
        if pending_count != declared_pending:
            raise ValueError("manifest pending_case_count does not match human_labels.jsonl")

    def split_case_ids(self) -> dict[str, set[str]]:
        return {
            split: {
                str(case["case_id"])
                for case in self.generation_cases
                if case["split"] == split
            }
            for split in VALID_SPLITS
        }

    def approved_human_labels(self) -> list[dict[str, Any]]:
        return [label for label in self.human_labels if label["review_status"] == "approved"]

    def require_human_gold_case_ids(self) -> set[str]:
        """Return approved case IDs or reject an unreviewed dataset."""
        approved = self.approved_human_labels()
        if not approved:
            raise RuntimeError(
                "human_gold_eval requires at least one independently approved V2 label; "
                "all generated labels are currently pending"
            )
        return {str(label["case_id"]) for label in approved}


def load_v2_dataset(root: Path = DATASET_DIR) -> EvaluationV2Dataset:
    return EvaluationV2Dataset.load(root)
