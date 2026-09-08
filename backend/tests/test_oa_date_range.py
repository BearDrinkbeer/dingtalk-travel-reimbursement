import hashlib
import json
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pytest
from test_oa_reimbursement_payload import _attachments, _component, _schema, _source

from app.core.errors import ApiError
from app.integrations.dingtalk.workflow import WorkflowFormValue
from app.services.oa_date_range import parse_date_range
from app.services.oa_reimbursement import _form_value_matches
from app.services.oa_reimbursement_payload import (
    build_create_command,
    build_snapshot,
    parse_create_command,
    parse_snapshot,
    serialize_create_command,
    serialize_snapshot,
)
from app.services.oa_template_profiles import (
    validate_template_mapping,
    validate_travel_template_mapping,
)
from app.services.travel_approvals import travel_approval_dates


def range_source():
    source = _source()
    contract = source.catalog.reimbursement
    component = replace(
        _component("range", "DDDateRangeField", '["开始时间","结束时间"]'),
        value_format="yyyy-MM-dd",
        unit="天",
    )
    components = tuple(
        c
        for c in contract.schema.components
        if c.component_id
        not in {
            "component-startDate",
            "component-endDate",
            "component-durationDays",
        }
    ) + (component,)
    schema = _schema(contract.process_code, components)
    mapping = dict(contract.mappings)
    mapping.update(dict.fromkeys(("startDate", "endDate", "durationDays"), "range"))
    return replace(
        source,
        catalog=replace(
            source.catalog,
            reimbursement=replace(
                contract,
                schema=schema,
                mappings=mapping,
            ),
        ),
    )


def test_native_range_command_emits_one_field_and_roundtrips():
    source = range_source()
    contract = source.catalog.reimbursement
    assert validate_template_mapping(contract.schema, contract.mappings) == contract.mappings
    snapshot = build_snapshot(source)
    command = build_create_command(snapshot, _attachments(snapshot))
    ranges = [v for v in command.form_values if v.component_type == "DDDateRangeField"]
    assert len(command.form_values) == 8
    assert len(ranges) == 1
    assert ranges[0].value == '["2026-09-01","2026-09-03"]'
    assert parse_create_command(serialize_create_command(command)) == command
    assert parse_snapshot(serialize_snapshot(snapshot)) == snapshot
    actual = WorkflowFormValue(
        "range", ranges[0].name, "DDDateRangeField", '[ "2026-09-01", "2026-09-03" ]', None, None
    )
    assert _form_value_matches(ranges[0], actual)
    for duration in (2, 3, "2", "3"):
        import json

        returned = replace(actual, value=json.dumps(["2026-09-01", "2026-09-03", duration]))
        assert _form_value_matches(ranges[0], returned)
    assert not _form_value_matches(ranges[0], replace(actual, value='["2026-09-01","2026-09-04"]'))
    with pytest.raises((ApiError, ValueError)):
        parse_snapshot(
            serialize_snapshot(snapshot).replace('"snapshotVersion":5', '"snapshotVersion":4')
        )


def test_travel_range_read_and_mapping():
    contract = range_source().catalog.reimbursement
    mapping = {"startDate": "range", "endDate": "range"}
    assert validate_travel_template_mapping(contract.schema, mapping) == mapping
    value = WorkflowFormValue(
        "range", "日期", "DDDateRangeField", '["2026-09-01","2026-09-03"]', None, None
    )
    assert travel_approval_dates(
        SimpleNamespace(form_values=(value,)),
        start_date_component_id="range",
        end_date_component_id="range",
    ) == (date(2026, 9, 1), date(2026, 9, 3))


def test_old_version_four_individual_dates_still_roundtrip():
    snapshot = build_snapshot(_source()).model_copy(update={"snapshot_version": 4})
    raw = serialize_snapshot(snapshot)
    # Pin canonical bytes independently of the parser round-trip: adding a
    # defaulted v5 field must not silently change persisted v4 content hashes.
    assert hashlib.sha256(raw.encode()).hexdigest() == (
        "9a0878381c1568a051c2231b870663de12d7dddbc047f639864b85a4d25c7b41"
    )
    content = json.loads(raw)
    assert all(
        "travelTypeComponentId" not in profile and "travelTypeMappings" not in profile
        for profile in content["template"]["travelProfiles"]
    )
    assert all("sourceTravelTypeValue" not in item for item in content["relatedApprovals"])
    assert serialize_snapshot(parse_snapshot(raw)) == raw
    assert len(build_create_command(snapshot, _attachments(snapshot)).form_values) == 10


def test_partial_range_mapping_rejected():
    source = range_source()
    contract = source.catalog.reimbursement
    schema = replace(
        contract.schema,
        components=contract.schema.components
        + (_component("separate-duration", "NumberField", "时长"),),
    )
    mapping = {**contract.mappings, "durationDays": "separate-duration"}
    with pytest.raises(ApiError, match="同一个日期区间"):
        validate_template_mapping(schema, mapping)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "{}",
        "[]",
        '["2026-09-03","2026-09-01"]',
        '["2026-09-01","2026-09-03",3,"天"]',
        '["20260901","20260903"]',
        '["2026-02-30","2026-03-01"]',
    ],
)
def test_range_rejects_unconfirmed_or_invalid_shapes(raw):
    assert parse_date_range(raw) is None


def test_real_dingtalk_readback_includes_duration():
    from datetime import date

    assert parse_date_range('["2026-07-04","2026-07-07","4"]') == (
        date(2026, 7, 4),
        date(2026, 7, 7),
    )
    assert parse_date_range('["2026-07-04","2026-07-07","99"]') is None


def test_production_numeric_elapsed_days_readback():
    from datetime import date

    assert parse_date_range('["2026-06-30","2026-07-07",7]') == (
        date(2026, 6, 30),
        date(2026, 7, 7),
    )


@pytest.mark.parametrize("duration", [True, False, -1, 1.5, 99, "7天", None, {}, []])
def test_range_rejects_invalid_duration_metadata(duration):
    import json

    assert parse_date_range(json.dumps(["2026-06-30", "2026-07-07", duration])) is None
