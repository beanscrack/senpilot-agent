import pytest
from regulatory_agent.uarb import extract_counts, safe_filename


def test_all_category_counts_and_commas():
    assert sum(extract_counts(['Exhibits - 1,234','Key Documents - 3','Other Documents - 0','Transcripts - 2','Recordings - 1']).values())==1240


def test_missing_count_fails_instead_of_claiming_zero():
    with pytest.raises(RuntimeError):
        extract_counts(['Exhibits - 5'])


@pytest.mark.parametrize('filename',['../escape.pdf','C:\\secret.pdf','CON','a/b/c.pdf'])
def test_download_name_cannot_escape_directory(filename):
    cleaned=safe_filename(filename)
    assert '/' not in cleaned and '\\' not in cleaned and ':' not in cleaned
    assert cleaned != 'CON'
