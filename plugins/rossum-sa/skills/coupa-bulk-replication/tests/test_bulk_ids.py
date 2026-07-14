import coupa_bulk_import as cbi


def test_sets_id_from_id_key_raw_value():
    recs, missing = cbi.assign_ids([{"id": 42, "v": "a"}, {"id": "A-7", "v": "b"}], "id")
    assert recs[0]["_id"] == 42          # int stays int
    assert recs[1]["_id"] == "A-7"       # string stays string — no coercion
    assert missing == 0


def test_missing_id_left_without_underscore_id():
    recs, missing = cbi.assign_ids([{"v": "no-id"}, {"id": None, "v": "null-id"}], "id")
    assert "_id" not in recs[0]
    assert "_id" not in recs[1]          # None must NOT become _id: null
    assert missing == 2


def test_empty_page():
    assert cbi.assign_ids([], "id") == ([], 0)


def test_falsy_ids_treated_as_missing():
    recs, missing = cbi.assign_ids(
        [{"id": "", "v": "empty-id"}, {"id": 0, "v": "zero-id"}], "id"
    )
    assert "_id" not in recs[0]          # "" must NOT become _id: ""
    assert "_id" not in recs[1]          # 0 must NOT become _id: 0
    assert missing == 2
