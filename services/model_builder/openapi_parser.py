"""OpenAPI 3.0 specification parser for extracting workflow operations and links."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})
PARAM_SEGMENT_REGEX = re.compile(r"^\{.*\}$")


@dataclass
class CandidateOperation:
    """Parsed OpenAPI operation candidate for workflow model synthesis."""

    operation_id: str
    method: str
    path_template: str
    resource_type: str | None
    security_roles: list[str] = field(default_factory=list)
    links: list[dict[str, Any]] = field(default_factory=list)

    @property
    def link_operation_ids(self) -> list[str]:
        """Convenience list of target operationId strings declared in links."""
        return [
            link["operation_id"]
            for link in self.links
            if isinstance(link, dict) and link.get("operation_id")
        ]

    def to_dict(self) -> dict[str, Any]:
        """Return dictionary representation."""
        return {
            "operation_id": self.operation_id,
            "method": self.method,
            "path_template": self.path_template,
            "resource_type": self.resource_type,
            "security_roles": list(self.security_roles),
            "links": list(self.links),
        }


class OpenAPIParser:
    """Parses OpenAPI 3.0 specifications into CandidateOperation instances."""

    @staticmethod
    def infer_resource_type(path_template: str) -> str | None:
        """Infer resource type from path segment preceding an identifier parameter.

        For example, /orders/{id}/ship or /orders/{order_id}/address yields 'orders'.
        If no parameter segment exists or no preceding segment exists, returns None.
        """
        segments = [s for s in path_template.strip("/").split("/") if s]
        for idx, segment in enumerate(segments):
            if PARAM_SEGMENT_REGEX.match(segment):
                if idx > 0:
                    preceding = segments[idx - 1]
                    # Verify preceding segment is a valid static identifier
                    if not PARAM_SEGMENT_REGEX.match(preceding):
                        return preceding
                return None
        return None

    @staticmethod
    def _extract_security_roles(
        operation_def: dict[str, Any],
        spec_dict: dict[str, Any],
    ) -> list[str]:
        """Extract declared security roles or scopes from operation or spec level."""
        roles: list[str] = []
        sec_requirements = operation_def.get("security")
        if sec_requirements is None:
            sec_requirements = spec_dict.get("security", [])

        if isinstance(sec_requirements, list):
            for req in sec_requirements:
                if isinstance(req, dict):
                    for scopes in req.values():
                        if isinstance(scopes, list):
                            for scope in scopes:
                                if isinstance(scope, str) and scope not in roles:
                                    roles.append(scope)

        # Also support explicit x-roles / x-allowed-roles annotations if present
        for key in ("x-roles", "x-allowed-roles", "x-security-roles"):
            annotated = operation_def.get(key)
            if isinstance(annotated, list):
                for role in annotated:
                    if isinstance(role, str) and role not in roles:
                        roles.append(role)

        return roles

    @staticmethod
    def _extract_links(operation_def: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract declared response links.

        Returns an explicit empty list if no links are declared.
        """
        links: list[dict[str, Any]] = []
        responses = operation_def.get("responses", {})
        if not isinstance(responses, dict):
            return links

        for status_code, resp_def in responses.items():
            if not isinstance(resp_def, dict):
                continue
            declared_links = resp_def.get("links")
            if isinstance(declared_links, dict):
                for link_name, link_data in declared_links.items():
                    if isinstance(link_data, dict):
                        links.append(
                            {
                                "name": link_name,
                                "operation_id": link_data.get("operationId"),
                                "operation_ref": link_data.get("operationRef"),
                                "parameters": link_data.get("parameters", {}),
                                "description": link_data.get("description", ""),
                                "response_code": str(status_code),
                            }
                        )
        return links

    def parse_spec(self, spec_dict: dict[str, Any]) -> list[CandidateOperation]:
        """Parse OpenAPI 3.0 specification dictionary into candidate operations."""
        paths = spec_dict.get("paths", {})
        if not isinstance(paths, dict):
            return []

        operations: list[CandidateOperation] = []

        for path_template, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue

            resource_type = self.infer_resource_type(path_template)

            for method_name, op_def in path_item.items():
                if method_name.lower() not in HTTP_METHODS or not isinstance(op_def, dict):
                    continue

                http_method = method_name.upper()
                op_id = op_def.get("operationId") or f"{http_method}_{path_template}"
                security_roles = self._extract_security_roles(op_def, spec_dict)
                links = self._extract_links(op_def)

                operations.append(
                    CandidateOperation(
                        operation_id=str(op_id),
                        method=http_method,
                        path_template=path_template,
                        resource_type=resource_type,
                        security_roles=security_roles,
                        links=links,
                    )
                )

        return operations
