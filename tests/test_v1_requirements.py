from __future__ import annotations

import unittest
from typing import Any

from worthit.inspect import V1_REQUIREMENTS_REVISION, assess_v1_requirements


class V1RequirementsTest(unittest.TestCase):
    def assess(self, readme: str) -> dict[str, Any]:
        return assess_v1_requirements({"readme": readme, "readme_path": "README.md"})

    def test_optional_and_negated_requirements_do_not_block(self) -> None:
        result = self.assess(
            """\
Optional — compute embeddings on a server
Do not require external APIs
install [gpu] explicitly if needed
"""
        )

        self.assertEqual(result["revision"], V1_REQUIREMENTS_REVISION)
        self.assertEqual(result["classification"], "V1_ELIGIBLE")
        self.assertEqual(result["indicators"], [])

    def test_optional_wording_does_not_hide_required_credentials_or_service(self) -> None:
        readme = """\
Optional — compute embeddings on a server
Do not require external APIs
"""
        cases = {
            "api credential": (
                "An OpenAI API key is required for core operation.\n",
                "external_api_credential",
            ),
            "paid api": (
                "A paid OpenAI API subscription is required for core operation.\n",
                "external_api_credential",
            ),
            "core service": ("Redis must be running for every command.\n", "core_external_service"),
        }

        for label, (required_line, category) in cases.items():
            with self.subTest(label):
                result = self.assess(readme + required_line)
                self.assertEqual(result["classification"], "DEFER_V1")
                self.assertEqual(
                    [indicator["category"] for indicator in result["indicators"]],
                    [category],
                )

    def test_conditional_gpu_install_does_not_hide_required_cuda(self) -> None:
        result = self.assess("install [gpu] explicitly if needed; CUDA is required for core operation.\n")

        self.assertEqual(result["classification"], "DEFER_V1")
        self.assertEqual(
            [indicator["category"] for indicator in result["indicators"]],
            ["gpu"],
        )

    def test_unknown_and_large_core_model_downloads_remain_fail_closed(self) -> None:
        unknown = self.assess("On first run, downloads model weights.\n")
        large = self.assess("On first run, downloads 4 GB of model weights.\n")

        self.assertEqual(unknown["classification"], "INSUFFICIENT_INFORMATION")
        self.assertEqual(large["classification"], "DEFER_V1")
        self.assertEqual(
            [indicator["category"] for indicator in unknown["indicators"]],
            ["model_download_size"],
        )
        self.assertEqual(
            [indicator["category"] for indicator in large["indicators"]],
            ["large_model_download"],
        )


if __name__ == "__main__":
    unittest.main()
