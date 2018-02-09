# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import logging
import requests

from flask import Blueprint, current_app, redirect, render_template, session

from landoui.forms import RevisionForm
from landoui.helpers import is_user_authenticated, set_last_local_referrer

logger = logging.getLogger(__name__)

revisions = Blueprint('revisions', __name__)
revisions.before_request(set_last_local_referrer)


@revisions.route('/revisions/<revision_id>/<diff_id>', methods=('GET', 'POST'))
# This route is a GET only because the diff ID will be added via JavaScript
@revisions.route('/revisions/<revision_id>')
def revisions_handler(revision_id, diff_id=None):
    # Load Revision and Landing History
    try:
        revision = _get_revision(revision_id, diff_id)
        landing_statuses = _get_landing_statuses(revision_id)
    except requests.HTTPError as exc:
        if exc.response.status_code == 404:
            return render_template('revision/404.html'), 404
        elif exc.response.status_code == 400:
            error_msg = exc.response.json()['title']
            if error_msg == 'Diff not related to the revision':
                return render_template('revision/400_wrong_diff.html'), 400
        raise
    parent_revisions = _flatten_parent_revisions(revision)

    # Fetch any potential landing issues to display.
    # TODO: Save the dryrun confirmation token in the Revision form to be
    # used when making the real landing request. https://trello.com/c/dh0c0YTs.
    # TODO: We shouldn't check the dryrun result before a POST to actually
    # submit the landing request. We do this now because the UI would otherwise
    # lack data. When the landing request is submitted if there are any new
    # warnings or blockers, they are returned and should be used. If landing
    # is successful, we must redirect to the GET revision handler so that
    # fresh revision and landing history is loaded.
    # Also consider splitting into two routes: https://trello.com/c/Oo3RUzUR.
    dryrun_confirmation_token = None  # noqa
    warnings = []
    blockers = []
    if is_user_authenticated():
        dryrun_result = _get_landing_dryrun_result(revision)
        dryrun_confirmation_token = dryrun_result['confirmation_token']  # noqa
        warnings = dryrun_result['warnings']
        blockers = dryrun_result['blockers']

    # Creates a new form on GET or loads the submitted form on a POST
    form = RevisionForm()
    error = None
    if form.is_submitted():
        # If successful return the redirect to the GET page, if not then
        # handle errors. FIXME: currently crashes for the error cases,
        # though, better than the original silent failure.
        return _handle_submission(form, revision, landing_statuses)

    # Set the diff id explicitly to avoid timing conflicts with
    # revision diff IDs being updated
    form.diff_id.data = revision['diff']['id']

    return render_template(
        'revision/revision.html',
        revision=revision,
        landing_statuses=landing_statuses,
        parents=parent_revisions,
        form=form,
        warnings=warnings,
        blockers=blockers,
        error=error
    )


def _handle_submission(form, revision, landing_statuses):
    if form.validate():
        # TODO: Any more basic validation

        # Make request to API for landing
        diff_id = int(form.diff_id.data)
        land_response = requests.post(
            '{host}/landings'.format(host=current_app.config['LANDO_API_URL']),
            json={
                'revision_id': revision['id'],
                'diff_id': diff_id,
            },
            headers={
                # TODO:  Add Phabricator API key for private revisions
                # 'X-Phabricator-API-Key': '',
                'Authorization': 'Bearer {}'.format(session['access_token']),
                'Content-Type': 'application/json',
            }
        )
        logger.info(land_response.json(), 'revision.landing.response')

        if land_response.status_code == 202:
            redirect_url = '/revisions/{revision_id}/{diff_id}'.format(
                revision_id=revision['id'], diff_id=diff_id
            )
            return redirect(redirect_url)
        else:
            # TODO:  Push an error on to an error stack to show in UI
            pass
    else:
        # TODO
        # Return validation errors
        pass


def _get_revision(revision_id, diff_id):
    revision_api_url = '{host}/revisions/{revision_id}'.format(
        host=current_app.config['LANDO_API_URL'], revision_id=revision_id
    )
    result = requests.get(revision_api_url, params={'diff_id': diff_id})
    result.raise_for_status()
    return result.json()


def _get_landing_statuses(revision_id):
    landing_api_status_url = '{host}/landings'.format(
        host=current_app.config['LANDO_API_URL']
    )
    result = requests.get(
        landing_api_status_url, params={'revision_id': revision_id}
    )
    result.raise_for_status()
    return result.json()


def _get_landing_dryrun_result(revision):
    """Fetch any warnings or blockers for landing the revision.

    Queries Lando API's /landings/dryrun endpoint which tells us
    whether there are any issues before landings.

    Issues can one be either a warning or a blocker.
    Users may acknowledge a warning and continue landing.
    If there are any blockers, landing is blocked until those resolve.

    Lando API also provides a confirmation token which is expected when
    performing the real landing via the POST /landings endpoint. This token
    tells the API that we've properly tried a dryrun first and acknowledged
    any warnings.

    Returns a dictionary with the following format:
    {
        'confirmation_token': token,
        'warnings': [{'id': id, 'message': message}, ...],
        'blockers': [{'id': id, 'message': message}, ...]
    }
    """
    # TODO Replace me with a query to the real dryrun endpoint.
    # Tracked in: https://trello.com/c/dh0c0YTs/
    result = {
        'confirmation_token': 'placeholder',
        'warnings': [],
        'blockers': []
    }
    return result


def _flatten_parent_revisions(revision):
    """ Transforms a JSON tree of parent revisions into a flat array.

    Args:
        revision: A revision (hash) which has parent revisions, which
            can themselves have parent revisions, and so on.
    Returns:
        A new array containing the parent revisions in breath first order.
    """
    parents = revision.get('parent_revisions', [])
    parents_of_parents = []
    for parent in parents:
        parents_of_parents += _flatten_parent_revisions(parent)
    return parents + parents_of_parents
