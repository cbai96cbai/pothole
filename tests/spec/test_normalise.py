from pothole.spec import normalise


def test_board_normaliser_cases():
    assert normalise.board("01 BROOKLYN") == ("Brooklyn", 1)
    assert normalise.board("12 MANHATTAN") == ("Manhattan", 12)
    assert normalise.board("18 BROOKLYN") == ("Brooklyn", 18)
    assert normalise.board("95 STATEN ISLAND") is None
    assert normalise.board("Unspecified") is None
    assert normalise.board("0 Unspecified") is None
    assert normalise.board("55 BROOKLYN") is None
    assert normalise.board("04 STATEN ISLAND") is None
    assert normalise.board(None) is None


def test_event_boards_cases():
    assert normalise.event_boards(["1,"], "Manhattan") == [("Manhattan", 1)]
    assert normalise.event_boards(["1,2,"], "Manhattan") == [("Manhattan", 1), ("Manhattan", 2)]
    assert normalise.event_boards(["1,", "1,2,"], "Manhattan") == [
        ("Manhattan", 1),
        ("Manhattan", 2),
    ]
    assert normalise.event_boards(["13,"], "Manhattan") == []
    assert normalise.event_boards(["1,"], "Nowhere") == []


def test_event_boards_skips_missing_elements():
    assert normalise.event_boards([None, "2,"], "Manhattan") == [("Manhattan", 2)]
