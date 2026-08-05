import datetime
from dateutil import relativedelta
import requests
import os
from lxml import etree
import time

# ACCESS_TOKEN is the workflow's automatic secrets.GITHUB_TOKEN, so there is no PAT to create
# or rotate. That token can only see public data, so the repository and star counts cover
# public repositories only. Showing private repositories too would require a fine-grained PAT
# with All Repositories access and read:Metadata.
HEADERS = {'authorization': 'token '+ os.environ['ACCESS_TOKEN']}
USER_NAME = os.environ['USER_NAME'] # 'Prthmsh7', from github.repository_owner
QUERY_COUNT = {'user_getter': 0, 'follower_getter': 0, 'graph_repos_stars': 0}


def daily_readme(start_date):
    """
    Returns the length of time since the given date
    e.g. 'XX years, XX months, XX days'
    """
    diff = relativedelta.relativedelta(datetime.datetime.today(), start_date)
    return '{} {}, {} {}, {} {}{}'.format(
        diff.years, 'year' + format_plural(diff.years),
        diff.months, 'month' + format_plural(diff.months),
        diff.days, 'day' + format_plural(diff.days),
        ' 🎉' if (diff.months == 0 and diff.days == 0) else '')


def format_plural(unit):
    """
    Returns a properly formatted number
    e.g.
    'day' + format_plural(diff.days) == 5
    >>> '5 days'
    'day' + format_plural(diff.days) == 1
    >>> '1 day'
    """
    return 's' if unit != 1 else ''


def request_with_retry(query, variables, attempts=5):
    """
    POSTs a GraphQL query, retrying transient failures with exponential backoff.

    GitHub answers the occasional request with a 502 or throttles with a 403; both are
    temporary. Retrying in place is cheaper than failing the whole run. Returns the last
    response either way, so the caller still decides whether to raise.

    On a 403 the Retry-After header is honoured when present, since that is the API telling
    us exactly how long the secondary rate limit lasts.
    """
    delay = 2
    for attempt in range(attempts):
        request = requests.post('https://api.github.com/graphql', json={'query': query, 'variables': variables}, headers=HEADERS)
        if request.status_code not in (403, 429, 500, 502, 503):
            return request
        if attempt == attempts - 1:
            return request
        wait = int(request.headers.get('Retry-After', delay))
        print(f'   retrying after {request.status_code} in {wait}s (attempt {attempt + 1}/{attempts})')
        time.sleep(wait)
        delay = min(delay * 2, 60)
    return request


def simple_request(func_name, query, variables):
    """
    Returns a request, or raises an Exception if the response does not succeed.
    """
    request = request_with_retry(query, variables)
    if request.status_code == 200:
        return request
    raise Exception(func_name, ' has failed with a', request.status_code, request.text, QUERY_COUNT)


def graph_repos_stars(count_type, owner_affiliation, cursor=None):
    """
    Uses GitHub's GraphQL v4 API to return my total repository or star count.

    Forks are deliberately included, so the repository count matches the number GitHub itself
    shows on the profile page rather than a smaller figure that would need explaining.
    """
    query_count('graph_repos_stars')
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 100, after: $cursor, ownerAffiliations: $owner_affiliation) {
                totalCount
                edges {
                    node {
                        ... on Repository {
                            nameWithOwner
                            stargazers {
                                totalCount
                            }
                        }
                    }
                }
                pageInfo {
                    endCursor
                    hasNextPage
                }
            }
        }
    }'''
    variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
    request = simple_request(graph_repos_stars.__name__, query, variables)
    repositories = request.json()['data']['user']['repositories']
    if count_type == 'repos':
        return repositories['totalCount']
    # stars need every page, since the API only returns 100 repositories at a time
    total = stars_counter(repositories['edges'])
    if repositories['pageInfo']['hasNextPage']:
        total += graph_repos_stars(count_type, owner_affiliation, repositories['pageInfo']['endCursor'])
    return total


def stars_counter(data):
    """
    Count total stars in repositories owned by me

    GraphQL returns an edge with a null node for any repository the token cannot read —
    which GITHUB_TOKEN hits on private repos, since they are still counted in the
    connection. Skip those instead of crashing on None['stargazers'].
    """
    total_stars = 0
    for node in data:
        if node['node'] is None:
            continue
        total_stars += node['node']['stargazers']['totalCount']
    return total_stars


def user_getter(username):
    """
    Returns the account ID and creation time of the user
    """
    query_count('user_getter')
    query = '''
    query($login: String!){
        user(login: $login) {
            id
            createdAt
        }
    }'''
    variables = {'login': username}
    request = simple_request(user_getter.__name__, query, variables)
    return {'id': request.json()['data']['user']['id']}, request.json()['data']['user']['createdAt']


def follower_getter(username):
    """
    Returns the number of followers of the user
    """
    query_count('follower_getter')
    query = '''
    query($login: String!){
        user(login: $login) {
            followers {
                totalCount
            }
        }
    }'''
    request = simple_request(follower_getter.__name__, query, {'login': username})
    return int(request.json()['data']['user']['followers']['totalCount'])


def svg_overwrite(filename, age_data, repo_data, star_data, follower_data):
    """
    Parse SVG files and update elements with my age, repositories, stars and followers

    The length argument of each justify_format call reserves the column width for that value,
    so a row's total width stays at 5 + len(key) + length characters no matter how the value
    grows. Keep these in step with the key names in the SVG.
    """
    tree = etree.parse(filename)
    root = tree.getroot()
    justify_format(root, 'age_data', age_data, 49)       # key 'Uptime'
    justify_format(root, 'repo_data', repo_data, 50)     # key 'Repos'
    justify_format(root, 'star_data', star_data, 50)     # key 'Stars'
    justify_format(root, 'follower_data', follower_data, 46)  # key 'Followers'
    tree.write(filename, encoding='utf-8', xml_declaration=True)


def justify_format(root, element_id, new_text, length=0):
    """
    Updates and formats the text of the element, and modifes the amount of dots in the previous element to justify the new text on the svg
    """
    if isinstance(new_text, int):
        new_text = f"{'{:,}'.format(new_text)}"
    new_text = str(new_text)
    find_and_replace(root, element_id, new_text)
    just_len = max(0, length - len(new_text))
    if just_len <= 2:
        dot_map = {0: '', 1: ' ', 2: '. '}
        dot_string = dot_map[just_len]
    else:
        dot_string = ' ' + ('.' * just_len) + ' '
    find_and_replace(root, f"{element_id}_dots", dot_string)


def find_and_replace(root, element_id, new_text):
    """
    Finds the element in the SVG file and replaces its text with a new value
    """
    element = root.find(f".//*[@id='{element_id}']")
    if element is not None:
        element.text = new_text


def query_count(funct_id):
    """
    Counts how many times the GitHub GraphQL API is called
    """
    global QUERY_COUNT
    QUERY_COUNT[funct_id] += 1


def perf_counter(funct, *args):
    """
    Calculates the time it takes for a function to run
    Returns the function result and the time differential
    """
    start = time.perf_counter()
    funct_return = funct(*args)
    return funct_return, time.perf_counter() - start


def formatter(query_type, difference):
    """
    Prints a formatted time differential
    """
    print('{:<23}'.format('   ' + query_type + ':'), sep='', end='')
    print('{:>12}'.format('%.4f' % difference + ' s ')) if difference > 1 else print('{:>12}'.format('%.4f' % (difference * 1000) + ' ms'))


if __name__ == '__main__':
    """
    Prathmesh Shukla (Prthmsh7), 2026
    Adapted from Andrew Grant's (Andrew6rant) README generator, cut down to the stats that
    can be fetched directly. The original walked every commit in every repository to total
    lines of code, which needed a persistent cache and thousands of API calls; these four
    values come from three queries and no cache at all.
    """
    print('Calculation times:')
    user_data, user_time = perf_counter(user_getter, USER_NAME)
    OWNER_ID, acc_date = user_data
    formatter('account data', user_time)
    # 'Uptime' counts from the day the GitHub account was created, not a birthday
    age_data, age_time = perf_counter(daily_readme, datetime.datetime.strptime(acc_date, '%Y-%m-%dT%H:%M:%SZ'))
    formatter('account age', age_time)
    repo_data, repo_time = perf_counter(graph_repos_stars, 'repos', ['OWNER'])
    formatter('repositories', repo_time)
    star_data, star_time = perf_counter(graph_repos_stars, 'stars', ['OWNER'])
    formatter('stars', star_time)
    follower_data, follower_time = perf_counter(follower_getter, USER_NAME)
    formatter('followers', follower_time)

    svg_overwrite('dark_mode.svg', age_data, repo_data, star_data, follower_data)
    svg_overwrite('light_mode.svg', age_data, repo_data, star_data, follower_data)

    print('Total GitHub GraphQL API calls:', '{:>3}'.format(sum(QUERY_COUNT.values())))
    for funct_name, count in QUERY_COUNT.items(): print('{:<28}'.format('   ' + funct_name + ':'), '{:>6}'.format(count))
