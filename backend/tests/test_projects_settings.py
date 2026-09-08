from __future__ import annotations

from conftest import mock_login

from app.models.setting import Setting
from app.services.dingtalk import DepartmentIdentity, DingTalkIdentity
from app.services.sessions import create_session


def test_business_apis_require_auth_and_admin_guard(client_factory) -> None:
    client = client_factory(auth_mock_enabled=True)
    assert client.get("/api/projects").status_code == 401
    assert client.get("/api/settings").status_code == 401

    login = mock_login(client)
    assert client.get("/api/admin/projects").status_code == 403
    assert (
        client.post(
            "/api/admin/projects",
            json={"projectName": "不可创建"},
            headers={"X-CSRF-Token": login["csrfToken"]},
        ).status_code
        == 403
    )


def test_project_crud_search_validation_and_csrf(client_factory) -> None:
    client = client_factory(
        auth_mock_enabled=True,
        auth_mock_user_id="admin-1",
        admin_user_ids="admin-1",
    )
    login = mock_login(client)
    csrf = login["csrfToken"]

    assert (
        client.post(
            "/api/admin/projects",
            json={"projectCode": "P-001", "projectName": "测试项目"},
        ).status_code
        == 403
    )
    created = client.post(
        "/api/admin/projects",
        json={"projectCode": " P-001 ", "projectName": " 测试项目 ", "enabled": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    project = created.json()["data"]
    assert project == {
        "id": project["id"],
        "projectCode": "P-001",
        "projectName": "测试项目",
        "enabled": True,
    }

    duplicate = client.post(
        "/api/admin/projects",
        json={"projectCode": "p-001", "projectName": "另一个名称"},
        headers={"X-CSRF-Token": csrf},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "PROJECT_ALREADY_EXISTS"
    blank = client.post(
        "/api/admin/projects",
        json={"projectName": "   "},
        headers={"X-CSRF-Token": csrf},
    )
    assert blank.status_code == 422

    assert client.get("/api/projects?q=p-00").json()["data"] == [project]
    assert client.get("/api/projects?q=%25").json()["data"] == []
    disabled = client.put(
        f"/api/admin/projects/{project['id']}",
        json={"projectCode": "P-001", "projectName": "测试项目", "enabled": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert disabled.status_code == 200
    assert client.get("/api/projects").json()["data"] == []
    assert len(client.get("/api/admin/projects").json()["data"]) == 1

    deleted = client.delete(
        f"/api/admin/projects/{project['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert deleted.status_code == 200
    assert client.get("/api/admin/projects").json()["data"] == []
    assert (
        client.delete(
            f"/api/admin/projects/{project['id']}",
            headers={"X-CSRF-Token": csrf},
        ).status_code
        == 404
    )


def test_settings_defaults_and_admin_update_use_decimal_string(client_factory) -> None:
    user = client_factory(auth_mock_enabled=True)
    mock_login(user)
    assert user.get("/api/settings").json()["data"] == {
        "appTitle": "智能差旅费报销申请",
        "subsidyRates": {
            "business": "100.00",
            "short_term_project": "100.00",
            "long_term_project": "150.00",
            "same_city_project": "50.00",
            "internal": "100.00",
            "overseas": "0.00",
        },
        "calculationMode": "half_day_12",
    }
    default_rates = {
        "business": "100.00",
        "short_term_project": "100.00",
        "long_term_project": "150.00",
        "same_city_project": "50.00",
        "internal": "100.00",
        "overseas": "0.00",
    }
    assert (
        user.put(
            "/api/admin/settings",
            json={
                "appTitle": "智能差旅费报销申请",
                "adminUserIds": [],
                "subsidyRates": default_rates,
                "calculationMode": "half_day_12",
            },
            headers={"X-CSRF-Token": user.get("/api/me").json()["data"]["csrfToken"]},
        ).status_code
        == 403
    )

    admin = client_factory(
        auth_mock_enabled=True,
        auth_mock_user_id="admin-1",
        admin_user_ids="admin-1",
    )
    csrf = mock_login(admin)["csrfToken"]
    updated_rates = default_rates | {
        "business": "88.50",
        "long_term_project": "160.00",
        "overseas": "120.00",
    }
    updated = admin.put(
        "/api/admin/settings",
        json={
            "appTitle": "企业差旅费报销",
            "adminUserIds": ["admin-2"],
            "subsidyRates": updated_rates,
            "calculationMode": "half_day_12",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["subsidyRates"] == updated_rates
    assert updated.json()["data"]["appTitle"] == "企业差旅费报销"
    assert updated.json()["data"]["adminUserIds"] == ["admin-2"]
    assert updated.json()["data"]["environmentAdminUserIds"] == ["admin-1"]
    assert admin.get("/api/config/public").json()["data"]["appTitle"] == "企业差旅费报销"
    assert (
        admin.put(
            "/api/admin/settings",
            json={
                "appTitle": "企业差旅费报销",
                "adminUserIds": ["admin-2"],
                "subsidyRates": default_rates | {"business": "88.501"},
                "calculationMode": "half_day_12",
            },
            headers={"X-CSRF-Token": csrf},
        ).status_code
        == 422
    )
    assert (
        admin.put(
            "/api/admin/settings",
            json={
                "appTitle": "企业差旅费报销",
                "adminUserIds": ["admin-2"],
                "subsidyRates": default_rates,
                "calculationMode": "policy_engine",
            },
            headers={"X-CSRF-Token": csrf},
        ).status_code
        == 422
    )

    for invalid_rate in [88.5, 0, "0", "-0.01", "10000.01", " 88.50", "88.501"]:
        rejected = admin.put(
            "/api/admin/settings",
            json={
                "subsidyRates": default_rates | {"business": invalid_rate},
                "appTitle": "企业差旅费报销",
                "adminUserIds": ["admin-2"],
                "calculationMode": "half_day_12",
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert rejected.status_code == 422, (invalid_rate, rejected.text)
        assert rejected.json()["error"]["code"] == "VALIDATION_ERROR"

    maximum = admin.put(
        "/api/admin/settings",
        json={
            "appTitle": "企业差旅费报销",
            "adminUserIds": ["admin-2"],
            "subsidyRates": default_rates | {"business": "10000.00"},
            "calculationMode": "half_day_12",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert maximum.status_code == 200
    assert maximum.json()["data"]["subsidyRates"]["business"] == "10000.00"

    zero_overseas = admin.put(
        "/api/admin/settings",
        json={
            "appTitle": "企业差旅费报销",
            "adminUserIds": ["admin-2"],
            "subsidyRates": default_rates | {"overseas": "0.00"},
            "calculationMode": "half_day_12",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert zero_overseas.status_code == 200


def test_environment_admin_can_add_and_remove_runtime_administrators(client_factory) -> None:
    client = client_factory(
        auth_mock_enabled=True,
        auth_mock_user_id="admin-1",
        admin_user_ids="admin-1",
    )
    environment_admin = mock_login(client)
    current = client.get("/api/admin/settings").json()["data"]
    writable = {
        "appTitle": current["appTitle"],
        "subsidyRates": current["subsidyRates"],
        "calculationMode": current["calculationMode"],
    }
    added = client.put(
        "/api/admin/settings",
        json=writable | {"adminUserIds": ["admin-2"]},
        headers={"X-CSRF-Token": environment_admin["csrfToken"]},
    )
    assert added.status_code == 200

    identity = DingTalkIdentity(
        user_id="admin-2",
        union_id="union-admin-2",
        name="新增管理员",
        departments=(DepartmentIdentity(id="100", name="测试部门"),),
    )
    with client.app.state.database_session_factory() as database:
        _, session_token, csrf_token = create_session(
            database,
            client.app.state.settings,
            identity,
            None,
        )
    client.cookies.set(client.app.state.settings.session_cookie_name, session_token)

    assert client.get("/api/admin/settings").status_code == 200
    removed = client.put(
        "/api/admin/settings",
        json=writable | {"adminUserIds": []},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert removed.status_code == 200
    assert removed.json()["data"]["environmentAdminUserIds"] == ["admin-1"]
    assert client.get("/api/admin/settings").status_code == 403


def test_corrupt_subsidy_setting_fails_closed_without_logging_value(client_factory, capsys) -> None:
    client = client_factory(auth_mock_enabled=True)
    csrf = mock_login(client)["csrfToken"]
    assert client.get("/api/settings").status_code == 200
    corrupt_value = "secret-corrupt-value"
    with client.app.state.database_session_factory() as database:
        setting = database.get(Setting, "subsidy_business_per_day")
        assert setting is not None
        setting.value = corrupt_value
        database.commit()

    settings_response = client.get("/api/settings")
    calculation_response = client.post(
        "/api/calculate/subsidy",
        json={
            "tripType": "business",
            "startDate": "2026-06-30",
            "startTime": "09:00",
            "endDate": "2026-07-07",
            "endTime": "18:00",
        },
        headers={"X-CSRF-Token": csrf},
    )

    for response in (settings_response, calculation_response):
        assert response.status_code == 500
        assert response.json()["error"] == {
            "code": "INVALID_SYSTEM_CONFIGURATION",
            "message": "系统补助配置无效，请联系管理员",
        }
    logged = capsys.readouterr().err
    assert "subsidy_business_per_day" in logged
    assert corrupt_value not in logged


def test_legacy_single_rate_is_safely_migrated_to_old_automatic_types(client_factory) -> None:
    client = client_factory(auth_mock_enabled=True)
    with client.app.state.database_session_factory() as database:
        database.query(Setting).delete()
        database.add(Setting(key="subsidy_per_day", value="88.50"))
        database.add(Setting(key="calculation_mode", value="half_day_12"))
        database.commit()
    mock_login(client)

    response = client.get("/api/settings")

    assert response.status_code == 200
    assert response.json()["data"]["subsidyRates"] == {
        "business": "88.50",
        "short_term_project": "88.50",
        "long_term_project": "150.00",
        "same_city_project": "50.00",
        "internal": "100.00",
        "overseas": "0.00",
    }
