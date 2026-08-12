"""Compatibility exports for the extracted Yomitan archive reader."""

from saitenka_dict.archive import classify_zip, read_json_bank, title_of, zip_roles

_title_of = title_of

__all__ = ["_title_of", "classify_zip", "read_json_bank", "zip_roles"]
