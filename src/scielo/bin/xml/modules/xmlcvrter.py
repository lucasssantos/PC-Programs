# coding=utf-8

import os
import shutil
from datetime import datetime

import serial_files
import fs_utils
import utils
import html_reports
import dbm_isis

import xc_models
import pkg_reports
import xml_utils
import xml_versions
import xpmaker
import xc
import xc_config

import attributes


converter_report_lines = []
CURRENT_PATH = os.path.dirname(os.path.realpath(__file__)).replace('\\', '/')

CONFIG_PATH = CURRENT_PATH + '/../config/'
converter_env = None


categories_messages = {
    'converted': 'converted', 
    'rejected': 'rejected', 
    'not converted': 'not converted', 
    'skept': 'skept conversion', 
    'deleted ex-aop': 'deleted ex-aop', 
    'deleted incorrect order': 'deleted incorrect order', 
    'not deleted ex-aop': 'not deleted ex-aop', 
    'new aop': 'aop version', 
    'new doc': 'doc has no aop', 
    'ex aop': 'aop is published in an issue', 
    'matched aop': 'doc has aop version', 
    'partially matched aop': 'doc has aop version partially matched (title/author are similar)', 
    'aop missing PID': 'doc has aop version which has no PID', 
    'unmatched aop': 'doc has an invalid aop version (title/author are not the same)', 
}


class ConverterEnv(object):

    def __init__(self):
        self.version = None
        self.db_issue = None
        self.db_article = None
        self.db_isis = None
        self.local_web_app_path = None
        self.serial_path = None
        self.is_windows = None
        self.org_manager = None


def register_log(message):
    if not '<' in message:
        message = html_reports.p_message(message)
    converter_report_lines.append(message)


def find_i_record(issue_label, print_issn, e_issn):
    i_record = None
    issues_records = converter_env.db_issue.search(issue_label, print_issn, e_issn)
    if len(issues_records) > 0:
        i_record = issues_records[0]
    return i_record


def find_issue_models(issue_label, p_issn, e_issn):
    #print(issue_data)

    i_record = None
    issue_models = None
    msg = None

    if issue_label is None:
        msg = html_reports.p_message('FATAL ERROR: Unable to identify the article\'s issue')
    else:
        i_record = find_i_record(issue_label, p_issn, e_issn)

        if i_record is None:
            msg = html_reports.p_message('FATAL ERROR: Issue ' + issue_label + ' is not registered in ' + converter_env.db_issue.db_filename + ' using the ISSN: ' + ' or'.join([i for i in [p_issn, e_issn] if i is not None]) + '.')
        else:
            issue_models = xc_models.IssueModels(i_record)

    return (issue_models, msg)


def get_complete_issue_items(issue_files, pkg_path, registered_articles, pkg_articles):
    #actions = {'add': [], 'skip-update': [], 'update': [], '-': [], 'changed order': []}
    xml_doc_actions = {}
    complete_issue_items = {}
    for name in registered_articles.keys():
        if not name in pkg_articles.keys():
            xml_doc_actions[name] = '-'
            complete_issue_items[name] = registered_articles[name]
    changed_orders = {}
    for name, article in pkg_articles.items():
        action = 'add'
        if name in registered_articles.keys():
            action = 'update'
            if converter_env.skip_identical_xml:
                if open(issue_files.base_source_path + '/' + name + '.xml', 'r').read() == open(pkg_path + '/' + name + '.xml', 'r').read():
                    action = 'skip-update'
            if action == 'update':
                if registered_articles[name].order != pkg_articles[name].order:
                    changed_orders[name] = (registered_articles[name].order, pkg_articles[name].order)
        xml_doc_actions[name] = action
        if action == 'skip-update':
            complete_issue_items[name] = registered_articles[name]
        else:
            complete_issue_items[name] = pkg_articles[name]
    return (complete_issue_items, xml_doc_actions, changed_orders)


def complete_issue_items_row(article, action, result, creation_date, last_update, source, notes=''):
    labels = ['name', 'package or database (creation date | last update)', 'order', 'notes', 'action', 'result', 'aop PID', 'toc section', '@article-type', 'article title']
    _source = source
    if source == 'registered':
        _source = 'database (' + str(creation_date) + ' | ' + str(last_update) + ')'
    values = []
    values.append(article.xml_name)
    values.append(_source)
    values.append(article.order)
    values.append(notes)
    values.append(action)
    values.append(result)
    values.append(article.previous_pid)
    values.append(article.toc_section)
    values.append(article.article_type)
    values.append(article.title)
    return (labels, values)


def display_status_before_xc(registered_articles, pkg_articles, xml_doc_actions, status_column_label='action'):
    orders = [article.order for article in registered_articles.values()] + [article.order if article.tree is not None else 'None' for article in pkg_articles.values()]

    orders = sorted(list(set([order for order in orders if order is not None])))

    sorted_registered = pkg_reports.articles_sorted_by_order(registered_articles)
    sorted_package = pkg_reports.articles_sorted_by_order(pkg_articles)
    items = []

    for order in orders:
        action = ''
        if order in sorted_registered.keys():
            for article in sorted_registered[order]:
                action = xml_doc_actions[article.xml_name]
                _notes = ''
                if action == 'update':
                    if registered_articles[article.xml_name].order != pkg_articles[article.xml_name].order:
                        action = 'delete'
                        _notes = 'new order=' + pkg_articles[article.xml_name].order
                labels, values = complete_issue_items_row(article, action, '', article.creation_date_display, article.last_update, 'registered', _notes)
                items.append(pkg_reports.label_values(labels, values))

        if order in sorted_package.keys():
            for article in sorted_package[order]:
                action = xml_doc_actions[article.xml_name]
                _notes = ''
                if registered_articles.get(article.xml_name) is not None:
                    if registered_articles[article.xml_name].order != pkg_articles[article.xml_name].order:
                        _notes = 'replacing ' + registered_articles[article.xml_name].order
                labels, values = complete_issue_items_row(article, action, '', '-', '-', 'package', _notes)
                items.append(pkg_reports.label_values(labels, values))
    return html_reports.sheet(labels, items, 'dbstatus', 'action')


def display_status_after_xc(previous_registered_articles, registered_articles, pkg_articles, xml_doc_actions, unmatched_orders):
    actions_result_labels = {'delete': 'deleted', 'update': 'updated', 'add': 'added', '-': '-', 'skip-update': 'skept', 'order changed': 'order changed', 'fail': 'update/add failed'}
    orders = sorted(list(set([article.order for article in previous_registered_articles.values()] + [article.order for article in registered_articles.values()] + [article.order if article.tree is not None else 'None' for article in pkg_articles.values()])))

    sorted_previous_registered = pkg_reports.articles_sorted_by_order(previous_registered_articles)
    sorted_registered = pkg_reports.articles_sorted_by_order(registered_articles)
    sorted_package = pkg_reports.articles_sorted_by_order(pkg_articles)

    items = []

    for order in orders:
        if order in sorted_registered.keys():
            # documento na base
            for article in sorted_registered[order]:
                action = xml_doc_actions[article.xml_name]
                result = actions_result_labels[action]
                _notes = ''
                if action == 'update':
                    if article.last_update is None:
                        result = 'error'
                    elif previous_registered_articles.get(article.xml_name).last_update == article.last_update:
                        result = 'error'
                    name = article.xml_name
                    if name in unmatched_orders.keys():
                        previous_order, new_order = unmatched_orders[name]
                        _notes = previous_order + '=>' + new_order
                        if result == 'error':
                            _notes = 'ERROR: Unable to replace ' + _notes
                labels, values = complete_issue_items_row(article, action, result, article.creation_date_display, article.last_update, 'registered', _notes)
                items.append(pkg_reports.label_values(labels, values))
        elif order in sorted_package.keys():
            # documento no pacote mas nao na base
            for article in sorted_package[order]:
                action = xml_doc_actions[article.xml_name]
                name = article.xml_name
                _notes = ''
                if name in unmatched_orders.keys():
                    previous_order, new_order = unmatched_orders[name]
                    _notes = previous_order + '=>' + new_order
                    _notes = 'ERROR: Unable to replace ' + _notes

                labels, values = complete_issue_items_row(article, action, 'error', '-', '-', 'package', _notes)
                items.append(pkg_reports.label_values(labels, values))
        elif order in sorted_previous_registered.keys():
            # documento anteriormente na base
            for article in sorted_previous_registered[order]:
                action = 'delete'
                name = article.xml_name
                _notes = ''
                if name in unmatched_orders.keys():
                    previous_order, new_order = unmatched_orders[name]
                    _notes = 'deleted ' + previous_order + '=> new: ' + new_order
                labels, values = complete_issue_items_row(article, '?', 'deleted', article.creation_date_display, article.last_update, 'excluded', _notes)
                items.append(pkg_reports.label_values(labels, values))
    return html_reports.sheet(labels, items, 'dbstatus', 'action')


def complete_issue_items_report(complete_issue_items, unmatched_orders):
    unmatched_orders_errors = ''
    if len(unmatched_orders) > 0:
        unmatched_orders_errors = ''.join([html_reports.p_message('WARNING: ' + name + "'s orders: " + ' -> '.join(list(order))) for name, order in unmatched_orders.items()])

    toc_f, toc_e, toc_w, toc_report = pkg_reports.validate_package(complete_issue_items, validate_order=True)
    toc_report = pkg_reports.get_toc_report_text(toc_f, toc_e, toc_w, unmatched_orders_errors + toc_report)

    return (toc_f, toc_report)


def normalized_package(src_path, report_path, wrk_path, pkg_path, version):
    xml_filenames = sorted([src_path + '/' + f for f in os.listdir(src_path) if f.endswith('.xml') and not 'incorrect' in f])
    articles, doc_file_info_items = xpmaker.make_package(xml_filenames, report_path, wrk_path, pkg_path, version, 'acron')
    return (xml_filenames, articles, doc_file_info_items)


def get_issue_models(articles):
    issue_label, p_issn, e_issn = xpmaker.package_issue(articles)
    return find_issue_models(issue_label, p_issn, e_issn)


def get_issue_files(issue_models, pkg_path):
    journal_files = serial_files.JournalFiles(converter_env.serial_path, issue_models.issue.acron)
    return serial_files.IssueFiles(journal_files, issue_models.issue.issue_label, pkg_path, converter_env.local_web_app_path)


def convert_package(src_path):
    display_title = False
    validate_order = True

    validations_report = ''
    xc_toc_report = ''

    conversion_status = {}
    pkg_quality_fatal_errors = 0
    xc_results_report = ''
    aop_results_report = ''
    before_conversion_report = ''
    after_conversion_report = ''
    acron_issue_label = 'unidentified ' + os.path.basename(src_path)[:-4]
    scilista_item = None
    issue_files = None

    report_components = {}

    dtd_files = xml_versions.DTDFiles('scielo', converter_env.version)
    result_path = src_path + '_xml_converter_result'
    wrk_path = result_path + '/work'
    pkg_path = result_path + '/scielo_package'
    report_path = result_path + '/errors'
    old_report_path = report_path
    old_result_path = result_path

    for path in [result_path, wrk_path, pkg_path, report_path]:
        if not os.path.isdir(path):
            os.makedirs(path)
            print(path)

    xml_filenames, pkg_articles, doc_file_info_items = normalized_package(src_path, report_path, wrk_path, pkg_path, converter_env.version)
    issue_models, issue_error_msg = get_issue_models(pkg_articles)

    report_components['issue-not-registered'] = issue_error_msg
    report_components['pkg_overview'] = pkg_reports.package_articles_overview(pkg_articles)
    selected_articles = None

    if issue_models is None:
        acron_issue_label = 'not_registered ' + os.path.basename(src_path)[:-4]
    else:
        issue_files = get_issue_files(issue_models, pkg_path)
        acron_issue_label = issue_models.issue.acron + ' ' + issue_models.issue.issue_label

        previous_registered_articles = get_registered_articles(issue_files)

        complete_issue_items, xml_doc_actions, unmatched_orders = get_complete_issue_items(issue_files, pkg_path, previous_registered_articles, pkg_articles)

        before_conversion_report = html_reports.tag('h3', 'Documents status in the package/database - before conversion')
        before_conversion_report += display_status_before_xc(previous_registered_articles, pkg_articles, xml_doc_actions)

        toc_f, xc_toc_report = complete_issue_items_report(complete_issue_items, unmatched_orders)

        if toc_f == 0:
            selected_articles = {}
            for xml_name, article in pkg_articles.items():
                if xml_doc_actions[xml_name] in ['add', 'update']:
                    selected_articles[xml_name] = article

        if selected_articles is None:
            xc_conclusion_msg = xc_conclusion_message(scilista_item, acron_issue_label, pkg_articles, selected_articles, conversion_status, pkg_quality_fatal_errors)

        elif len(selected_articles) > 0:

            #references_stats = pkg_reports.pkg_authors_and_affiliations_stats(pkg_articles)
            #references_stats += pkg_reports.pkg_references_stats(pkg_articles)

            pkg_quality_fatal_errors, articles_stats, articles_reports = pkg_reports.validate_pkg_items(converter_env.db_article.org_manager, selected_articles, doc_file_info_items, dtd_files, validate_order, display_title, xml_doc_actions)

            scilista_item, conversion_stats_and_reports, conversion_status, aop_status = convert_articles(issue_files, issue_models, pkg_articles, articles_stats, xml_doc_actions, previous_registered_articles, unmatched_orders)

            validations_report = html_reports.tag('h2', 'Detail Report')
            validations_report += pkg_reports.get_articles_report_text(articles, articles_reports, articles_stats, conversion_stats_and_reports)

            xc_conclusion_msg = xc_conclusion_message(scilista_item, acron_issue_label, pkg_articles, selected_articles, conversion_status, pkg_quality_fatal_errors)

            after_conversion_report = html_reports.tag('h3', 'Documents status in the package/database - after conversion')
            after_conversion_report += display_status_after_xc(previous_registered_articles, get_registered_articles(issue_files), pkg_articles, xml_doc_actions, unmatched_orders)

            xc_results_report = html_reports.tag('h3', 'Conversion results') + report_status(conversion_status, 'conversion')

            aop_results_report = 'this journal has no aop.'
            if not aop_status is None:
                aop_results_report = report_status(aop_status, 'aop-block')
            aop_results_report = html_reports.tag('h3', 'AOP status') + aop_results_report

            #sheets = pkg_reports.get_lists_report_text(articles_sheets)

            issue_files.save_reports(report_path)
            issue_files.save_source_files(pkg_path)
            report_path = issue_files.base_reports_path
            result_path = issue_files.issue_path

            if scilista_item is not None:
                issue_files.copy_files_to_local_web_app()
        else:
            xc_conclusion_msg = xc_conclusion_message(scilista_item, acron_issue_label, pkg_articles, selected_articles, conversion_status, pkg_quality_fatal_errors)

    report_location = report_path + '/xml_converter.html'

    report_components['xml-list'] = pkg_reports.xml_list(pkg_path, xml_filenames)
    report_components['db-overview'] = before_conversion_report + after_conversion_report

    report_components['summary-report'] += xc_conclusion_msg + xc_results_report + aop_results_report
    if converter_env.is_windows:
        report_components['summary-report'] += pkg_reports.processing_result_location(result_path)

    report_components['toc'] = xc_toc_report
    report_components['detail-report'] = validations_report

    f, e, w, content = pkg_reports.format_complete_report(result_path)
    if old_report_path in content:
        content = content.replace(old_report_path, report_path)

    email_subject = format_email_subject(scilista_item, selected_articles, pkg_quality_fatal_errors, f, e, w)

    pkg_reports.save_report(report_location, ['XML Conversion (XML to Database)', acron_issue_label], content)

    if not converter_env.is_windows:
        format_reports_for_web(report_path, pkg_path, acron_issue_label.replace(' ', '/'))
        fs_utils.delete_file_or_folder(src_path)

    if old_result_path != result_path:
        fs_utils.delete_file_or_folder(old_result_path)

    return (report_location, scilista_item, acron_issue_label, email_subject)


def old_convert_package(src_path):
    display_title = False
    validate_order = True

    validations_report = ''
    xc_toc_report = ''

    xc_conclusion_msg = ''
    conversion_status = {}
    pkg_quality_fatal_errors = 0
    xc_results_report = ''
    aop_results_report = ''
    before_conversion_report = ''
    after_conversion_report = ''
    acron_issue_label = 'unidentified ' + os.path.basename(src_path)[:-4]
    scilista_item = None
    issue_files = None

    dtd_files = xml_versions.DTDFiles('scielo', converter_env.version)
    result_path = src_path + '_xml_converter_result'
    wrk_path = result_path + '/work'
    pkg_path = result_path + '/scielo_package'
    report_path = result_path + '/errors'
    old_report_path = report_path
    old_result_path = result_path

    for path in [result_path, wrk_path, pkg_path, report_path]:
        if not os.path.isdir(path):
            os.makedirs(path)
            print(path)

    xml_filenames, pkg_articles, doc_file_info_items = normalized_package(src_path, report_path, wrk_path, pkg_path, converter_env.version)
    issue_models, issue_error_msg = get_issue_models(pkg_articles)

    pkg_overview_report = pkg_reports.package_articles_overview(pkg_articles)
    selected_articles = None

    if issue_models is None:
        acron_issue_label = 'not_registered ' + os.path.basename(src_path)[:-4]
    else:
        issue_files = get_issue_files(issue_models, pkg_path)
        acron_issue_label = issue_models.issue.acron + ' ' + issue_models.issue.issue_label

        previous_registered_articles = get_registered_articles(issue_files)

        complete_issue_items, xml_doc_actions, unmatched_orders = get_complete_issue_items(issue_files, pkg_path, previous_registered_articles, pkg_articles)

        before_conversion_report = html_reports.tag('h3', 'Documents status in the package/database - before conversion')
        before_conversion_report += display_status_before_xc(previous_registered_articles, pkg_articles, xml_doc_actions)

        toc_f, xc_toc_report = complete_issue_items_report(complete_issue_items, unmatched_orders)

        if toc_f == 0:
            selected_articles = {}
            for xml_name, article in pkg_articles.items():
                if xml_doc_actions[xml_name] in ['add', 'update']:
                    selected_articles[xml_name] = article

        if selected_articles is None:
            xc_conclusion_msg = xc_conclusion_message(scilista_item, acron_issue_label, pkg_articles, selected_articles, conversion_status, pkg_quality_fatal_errors)

        elif len(selected_articles) > 0:

            #references_stats = pkg_reports.pkg_authors_and_affiliations_stats(pkg_articles)
            #references_stats += pkg_reports.pkg_references_stats(pkg_articles)

            pkg_quality_fatal_errors, articles_stats, articles_reports = pkg_reports.validate_pkg_items(converter_env.db_article.org_manager, selected_articles, doc_file_info_items, dtd_files, validate_order, display_title, xml_doc_actions)

            scilista_item, conversion_stats_and_reports, conversion_status, aop_status = convert_articles(issue_files, issue_models, pkg_articles, articles_stats, xml_doc_actions, previous_registered_articles, unmatched_orders)

            validations_report = html_reports.tag('h2', 'Detail Report')
            validations_report += pkg_reports.get_articles_report_text(articles, articles_reports, articles_stats, conversion_stats_and_reports)

            xc_conclusion_msg = xc_conclusion_message(scilista_item, acron_issue_label, pkg_articles, selected_articles, conversion_status, pkg_quality_fatal_errors)

            after_conversion_report = html_reports.tag('h3', 'Documents status in the package/database - after conversion')
            after_conversion_report += display_status_after_xc(previous_registered_articles, get_registered_articles(issue_files), pkg_articles, xml_doc_actions, unmatched_orders)

            xc_results_report = html_reports.tag('h3', 'Conversion results') + report_status(conversion_status, 'conversion')

            aop_results_report = 'this journal has no aop.'
            if not aop_status is None:
                aop_results_report = report_status(aop_status, 'aop-block')
            aop_results_report = html_reports.tag('h3', 'AOP status') + aop_results_report

            #sheets = pkg_reports.get_lists_report_text(articles_sheets)

            issue_files.save_reports(report_path)
            issue_files.save_source_files(pkg_path)
            report_path = issue_files.base_reports_path
            result_path = issue_files.issue_path

            if scilista_item is not None:
                issue_files.copy_files_to_local_web_app()
        else:
            xc_conclusion_msg = xc_conclusion_message(scilista_item, acron_issue_label, pkg_articles, selected_articles, conversion_status, pkg_quality_fatal_errors)

    report_location = report_path + '/xml_converter.html'

    texts = []
    texts.append(html_reports.section('Package: XML list', pkg_reports.xml_list(pkg_path, xml_filenames)))
    texts.append(pkg_overview_report)
    texts.append(issue_error_msg)
    texts.append(xc_conclusion_msg)
    texts.append(before_conversion_report)
    texts.append(after_conversion_report)
    texts.append(xc_results_report)
    texts.append(aop_results_report)
    #texts.append(references_stats)
    texts.append(xc_toc_report)
    texts.append(validations_report)
    #texts.append(sheets)

    if converter_env.is_windows:
        texts.append(pkg_reports.processing_result_location(result_path))

    texts.append(html_reports.tag('p', 'Finished.'))

    content = html_reports.join_texts(texts)

    if old_report_path in content:
        content = content.replace(old_report_path, report_path)

    f, e, w = html_reports.statistics_numbers(content)

    email_subject = format_email_subject(scilista_item, selected_articles, pkg_quality_fatal_errors, f, e, w)

    if selected_articles is None:
        header_status = ''
    elif len(selected_articles) == 0:
        header_status = ''
    else:
        header_status = pkg_reports.statistics_and_subtitle(f, e, w)

    pkg_reports.save_report(report_location, ['XML Conversion (XML to Database)', acron_issue_label], header_status + content)

    if not converter_env.is_windows:
        format_reports_for_web(report_path, pkg_path, acron_issue_label.replace(' ', '/'))
        fs_utils.delete_file_or_folder(src_path)

    if old_result_path != result_path:
        fs_utils.delete_file_or_folder(old_result_path)

    return (report_location, scilista_item, acron_issue_label, email_subject)


def format_email_subject(scilista_item, selected_articles, pkg_quality_fatal_errors, f, e, w):
    inline_stats = '[' + ' | '.join([k + ': ' + v for k, v in [('fatal errors', str(f)), ('errors', str(e)), ('warnings', str(w))]]) + ']'
    if scilista_item is None:
        if selected_articles is None:
            email_subject_status = u"\u274C" + ' REJECTED ' + inline_stats
        elif len(selected_articles) == 0:
            email_subject_status = 'IGNORED'
        else:
            email_subject_status = u"\u274C" + ' REJECTED ' + inline_stats
    elif pkg_quality_fatal_errors > 0:
        email_subject_status = u"\u2713" + ' ' + u"\u270D" + ' ACCEPTED but corrections required ' + inline_stats
    else:
        email_subject_status = u"\u2705" + ' APPROVED ' + inline_stats
    return email_subject_status


def format_reports_for_web(report_path, pkg_path, issue_path):
    if not os.path.isdir(converter_env.local_web_app_path + '/htdocs/reports/' + issue_path):
        os.makedirs(converter_env.local_web_app_path + '/htdocs/reports/' + issue_path)

    for f in os.listdir(report_path):
        if f.endswith('.zip') or f == 'xml_converter.txt':
            os.unlink(report_path + '/' + f)
        else:
            content = open(report_path + '/' + f).read()
            if not isinstance(content, unicode):
                try:
                    content = content.decode('utf-8')
                except:
                    content = content.decode('iso-8859-1')
            content = content.replace('file:///' + pkg_path, '/img/revistas/' + issue_path)
            content = content.replace('file:///' + report_path, '/reports/' + issue_path)
            if isinstance(content, unicode):
                content = content.encode('utf-8')
            open(converter_env.local_web_app_path + '/htdocs/reports/' + issue_path + '/' + f, 'w').write(content)


def is_conversion_allowed(pub_year, ref_count, xml_f, xml_e, xml_w, data_f, data_e, data_w, conv_f, conv_e, conv_w):

    def max_score(quote, score):
        return ((score * quote) / 100) + 1
    doit = False
    score = (ref_count + 20)
    if conv_f == 0:
        if pub_year is not None:
            if pub_year[0:4].isdigit():
                if int(pub_year[0:4]) < (int(datetime.now().isoformat()[0:4]) - 1):
                    #doc anterior a dois anos atrás)
                    doit = True
        if doit is False:
            doit = True
            if converter_env.max_fatal_error is not None:
                if xml_f + data_f > max_score(converter_env.max_fatal_error, score):
                    doit = False
            if converter_env.max_error is not None:
                if xml_e + data_e > max_score(converter_env.max_error, score):
                    doit = False
            if converter_env.max_warning is not None:
                if xml_w + data_w > max_score(converter_env.max_warning, score):
                    doit = False
    return doit


def convert_articles(issue_files, issue_models, pkg_articles, articles_stats, xml_doc_actions, registered_articles, unmatched_orders):
    index = 0
    conversion_stats_and_reports = {}
    conversion_status = {}

    for k in ['converted', 'rejected', 'not converted', 'skept', 'deleted incorrect order']:
        conversion_status[k] = []

    n = '/' + str(len(pkg_articles))

    i_ahead_records = {}
    for db_filename in issue_files.journal_files.ahead_bases:
        year = os.path.basename(db_filename)[0:4]
        i_ahead_records[year] = find_i_record(year + 'nahead', issue_models.issue.issn_id, None)

    ahead_manager = xc_models.AheadManager(converter_env.db_isis, issue_files.journal_files, i_ahead_records)
    aop_status = None
    if ahead_manager.journal_has_aop():
        aop_status = {'deleted ex-aop': [], 'not deleted ex-aop': []}
    for xml_name in pkg_reports.sorted_xml_name_by_order(pkg_articles):
        article = pkg_articles[xml_name]
        index += 1

        item_label = str(index) + n + ' - ' + xml_name
        print(item_label)

        msg = ''

        if not xml_doc_actions[xml_name] in ['add', 'update']:
            msg += html_reports.tag('p', 'skept')
            conversion_status['skept'].append(xml_name)
            conv_stats = ''
        else:
            xml_stats, data_stats = articles_stats[xml_name]
            xml_f, xml_e, xml_w = xml_stats
            data_f, data_e, data_w = data_stats

            valid_ahead = None
            if aop_status is not None:
                valid_ahead, doc_ahead_status = ahead_manager.get_valid_ahead(article)
                if not doc_ahead_status in aop_status.keys():
                    aop_status[doc_ahead_status] = []
                aop_status[doc_ahead_status].append(xml_name)
                msg += aop_message(article, valid_ahead, doc_ahead_status)

                if valid_ahead is not None:
                    if doc_ahead_status in ['unmatched aop', 'aop missing PID']:
                        valid_ahead = None

            section_code, issue_validations_msg = validate_xml_issue_data(issue_models, article)

            if len(issue_validations_msg) > 0:
                msg += html_reports.tag('h4', 'Checking issue data')
                msg += issue_validations_msg
            conv_f, conv_e, conv_w = html_reports.statistics_numbers(msg)

            msg += html_reports.tag('h4', 'Converting xml to database')
            xc_result = 'None'
            if is_conversion_allowed(article.issue_pub_dateiso, len(article.references), xml_f, xml_e, xml_w, data_f, data_e, data_w, conv_f, conv_e, conv_w):
                article.section_code = section_code
                if valid_ahead is not None:
                    article._ahead_pid = valid_ahead.ahead_pid

                article_files = serial_files.ArticleFiles(issue_files, article.order, xml_name)

                creation_date = None if not xml_name in registered_articles else registered_articles[xml_name].creation_date

                saved = converter_env.db_article.create_id_file(issue_models.record, article, article_files, creation_date)
                if saved:
                    if xml_name in unmatched_orders.keys():
                        prev_order, curr_order = unmatched_orders[xml_name]
                        msg += html_reports.p_message('WARNING: Replacing orders: ' + prev_order + ' by ' + curr_order)
                        prev_article_files = serial_files.ArticleFiles(issue_files, prev_order, xml_name)
                        msg += html_reports.p_message('WARNING: Deleting ' + os.path.basename(prev_article_files.id_filename))
                        os.unlink(prev_article_files.id_filename)
                        conversion_status['deleted incorrect order'].append(prev_order)

                    if aop_status is not None:
                        if doc_ahead_status in ['matched aop', 'partially matched aop']:
                            saved, ahead_msg = ahead_manager.manage_ex_ahead(valid_ahead)
                            msg += ''.join([item for item in ahead_msg])
                            if saved:
                                aop_status['deleted ex-aop'].append(xml_name)
                                msg += html_reports.p_message('INFO: ex aop was deleted')
                            else:
                                aop_status['not deleted ex-aop'].append(xml_name)
                                msg += html_reports.p_message('ERROR: Unable to delete ex aop')
                    xc_result = 'converted'
                else:
                    xc_result = 'not converted'
            else:
                xc_result = 'rejected'
            conversion_status[xc_result].append(xml_name)
            if not xc_result in ['converted']:
                xc_result += '. FATAL ERROR!'
            msg += html_reports.p_message('Result: ' + xc_result)

        conv_f, conv_e, conv_w = html_reports.statistics_numbers(msg)
        title = html_reports.statistics_display(conv_f, conv_e, conv_w, True)
        conv_stats = html_reports.get_stats_numbers_style(conv_f, conv_e, conv_w)
        conversion_stats_and_reports[xml_name] = (conv_f, conv_e, conv_w, html_reports.collapsible_block(xml_name + 'conv', 'Converter validations: ' + title, msg, conv_stats))

    if ahead_manager.journal_has_aop():
        if len(aop_status['deleted ex-aop']) > 0:
            updated = ahead_manager.finish_manage_ex_ahead()
            if len(updated) > 0:
                aop_status['updated bases'] = updated
        aop_status['still aop'] = ahead_manager.still_ahead_items()
    scilista_item = None
    if len(conversion_status['not converted']) + len(conversion_status['rejected']) == 0:
        saved = converter_env.db_article.finish_conversion(issue_models.record, issue_files)
        if saved > 0:
            scilista_item = issue_models.issue.acron + ' ' + issue_models.issue.issue_label
            if not converter_env.is_windows:
                converter_env.db_article.generate_windows_version(issue_files)

    return (scilista_item, conversion_stats_and_reports, conversion_status, aop_status)


def aop_message(article, ahead, status):
    data = []
    msg_list = []
    if status == 'new aop':
        msg_list.append('INFO: This document is an "aop".')
    else:
        msg_list.append('Checking if ' + article.xml_name + ' has an "aop version"')
        if article.doi is not None:
            msg_list.append('Checking if ' + article.doi + ' has an "aop version"')

        if status == 'new doc':
            msg_list.append('WARNING: Not found an "aop version" of this document.')
        else:
            msg_list.append('WARNING: Found: "aop version"')
            if status == 'partially matched aop':
                msg_list.append('WARNING: the title/author of article and its "aop version" are similar.')
            elif status == 'aop missing PID':
                msg_list.append('ERROR: the "aop version" has no PID')
            elif status == 'unmatched aop':
                status = 'unmatched aop'
                msg_list.append('FATAL ERROR: the title/author of article and "aop version" are different.')

            data.append('doc title:' + article.title)
            data.append('aop title:' + ahead.article_title)
            data.append('doc first author:' + article.first_author_surname)
            data.append('aop first author:' + ahead.first_author_surname)
    msg = ''
    msg += html_reports.tag('h4', 'Checking existence of aop version')
    msg += ''.join([html_reports.p_message(item) for item in msg_list])
    msg += ''.join([html_reports.display_xml(item, html_reports.XML_WIDTH*0.9) for item in data])
    return msg


def get_registered_articles(issue_files):
    registered_issue_models, registered_articles_list = converter_env.db_article.registered_items(issue_files)
    registered_articles = {}
    for article in registered_articles_list:
        registered_articles[article.xml_name] = article
    return registered_articles


def report_status(status, style=None):
    text = ''
    for category in sorted(status.keys()):
        _style = style
        if len(status[category]) == 0:
            ltype = 'ul'
            list_items = ['None']
            _style = None
        else:
            ltype = 'ol'
            list_items = status[category]
        text += html_reports.format_list(categories_messages.get(category, category), ltype, list_items, _style)

    return text


def xc_conclusion_message(scilista_item, issue_label, pkg_articles, selected_articles, xc_status, pkg_quality_fatal_errors):
    total = len(selected_articles) if selected_articles is not None else 0
    converted = len(xc_status.get('converted', []))
    failed = total - converted
    app_site = converter_env.web_app_site if converter_env.web_app_site is not None else 'scielo web site'
    status = ''
    action = ''
    result = 'be updated/published on ' + app_site
    reason = ''
    if scilista_item is None:
        action = ' not'
        if selected_articles is None:
            status = 'FATAL ERROR'
            reason = 'because there are errors in the package.'
        elif len(selected_articles) == 0:
            status = 'WARNING'
            reason = 'because no document was changed.'
        elif failed > 0:
            status = 'FATAL ERROR'
            reason = 'because it is not complete (' + str(failed) + ' were not converted).'
        else:
            status = 'FATAL ERROR'
            reason = 'because it was unable to save the database.'
    else:
        if pkg_quality_fatal_errors > 0:
            status = 'WARNING'
            reason = ' even though there are some FATAL ERRORS. Note: These errors must be fixed in order to have good quality of bibliometric indicators and services.'
        else:
            status = 'OK'
            reason = ''

    text = status + ': ' + issue_label + ' will' + action + ' ' + result + ' ' + reason
    text = html_reports.tag('h2', 'Summary Report') + html_reports.p_message('converted: ' + str(converted) + '/' + str(total)) + html_reports.p_message(text)
    return text


def transfer_website_files(acron, issue_id, local_web_app_path, user, server, remote_web_app_path):
    # 'rsync -CrvK img/* user@server:/var/www/...../revistas'
    issue_id_path = acron + '/' + issue_id

    folders = ['/htdocs/img/revistas/', '/bases/pdf/', '/bases/xml/']

    for folder in folders:
        dest_path = remote_web_app_path + folder + issue_id_path
        source_path = local_web_app_path + folder + issue_id_path
        xc.run_remote_mkdirs(user, server, dest_path)
        xc.run_rsync(source_path, user, server, dest_path)


def transfer_report_files(acron, issue_id, local_web_app_path, user, server, remote_web_app_path):
    # 'rsync -CrvK img/* user@server:/var/www/...../revistas'
    issue_id_path = acron + '/' + issue_id

    folders = ['/htdocs/reports/']

    for folder in folders:
        dest_path = remote_web_app_path + folder + issue_id_path
        source_path = local_web_app_path + folder + issue_id_path
        xc.run_remote_mkdirs(user, server, dest_path)
        xc.run_rsync(source_path, user, server, dest_path)


def validate_xml_issue_data(issue_models, article):
    msg = []
    if article.tree is not None:
        validations = []
        validations.append(('journal title', article.journal_title, issue_models.issue.journal_title))
        validations.append(('journal id NLM', article.journal_id_nlm_ta, issue_models.issue.journal_id_nlm_ta))
        validations.append(('journal e-ISSN', article.journal_issns.get('epub'), issue_models.issue.journal_issns.get('epub')))
        validations.append(('journal print ISSN', article.journal_issns.get('ppub'), issue_models.issue.journal_issns.get('ppub')))
        validations.append(('issue label', article.issue_label, issue_models.issue.issue_label))
        validations.append(('issue date', article.issue_pub_dateiso[0:4], issue_models.issue.dateiso[0:4]))

        # check issue data
        for label, article_data, issue_data in validations:
            if article_data is None:
                article_data = 'None'
            elif isinstance(article_data, list):
                article_data = ' | '.join(article_data)
            if issue_data is None:
                issue_data = 'None'
            elif isinstance(issue_data, list):
                issue_data = ' | '.join(issue_data)
            if not article_data == issue_data:
                msg.append(html_reports.tag('h5', label))
                msg.append('FATAL ERROR: data mismatched. In article: "' + article_data + '" and in issue: "' + issue_data + '"')

        validations = []
        validations.append(('publisher', article.publisher_name, issue_models.issue.publisher_name))
        for label, article_data, issue_data in validations:
            if article_data is None:
                article_data = 'None'
            elif isinstance(article_data, list):
                article_data = ' | '.join(article_data)
            if issue_data is None:
                issue_data = 'None'
            elif isinstance(issue_data, list):
                issue_data = ' | '.join(issue_data)
            if utils.how_similar(article_data, issue_data) < 0.8:
                msg.append(html_reports.tag('h5', label))
                msg.append('FATAL ERROR: data mismatched. In article: "' + article_data + '" and in issue: "' + issue_data + '"')

        # section
        section_msg = []
        section_code, matched_rate, fixed_sectitle = issue_models.most_similar_section_code(article.toc_section)
        if matched_rate != 1:
            if not article.is_ahead:
                section_msg.append('Registered sections:\n' + '; '.join(issue_models.section_titles))
                if section_code is None:
                    section_msg.append('ERROR: ' + article.toc_section + ' is not a registered section.')
                else:
                    section_msg.append('WARNING: section replaced: "' + fixed_sectitle + '" (instead of "' + article.toc_section + '")')

        # @article-type
        if fixed_sectitle is not None:
            _sectitle = fixed_sectitle
        else:
            _sectitle = article.toc_section
        article_type_msg = validate_article_type_and_section(article.article_type, _sectitle)
        if len(article_type_msg) > 0 or len(section_msg) > 0:
            msg.append(html_reports.tag('h5', 'section'))
            msg.append(article.toc_section)
            for m in section_msg:
                msg.append(m)
            msg.append(html_reports.tag('h5', 'article-type'))
            msg.append(article.article_type)
            if len(article_type_msg) > 0:
                msg.append(article_type_msg)

    msg = ''.join([html_reports.p_message(item) for item in msg])
    return (section_code, msg)


def validate_article_type_and_section(article_type, article_section):
    #DOCTOPIC_IN_USE
    msg = ''
    _sectitle = attributes.normalize_section_title(article_section)
    _article_type = attributes.normalize_section_title(article_type)
    rate = compare_article_type_and_section(_article_type, _sectitle)
    rate2, similars = utils.most_similar(utils.similarity(attributes.DOCTOPIC_IN_USE, _sectitle))
    if rate < 0.5 and rate2 < 0.5:
        if not _article_type in _sectitle:
            msg = 'WARNING: Check if ' + article_type + ' is a valid value for @article-type. <!-- ' + _sectitle + ' -->'
    elif rate2 > rate:
        if not article_type in similars:
            msg = 'ERROR: Check @article-type. Maybe it should be ' + ' or '.join(similars) + ' instead of ' + article_type + '.'
    return msg


def compare_article_type_and_section(article_type, article_section):
    return utils.how_similar(article_section, article_type.replace('-', ' '))


def queue_packages(download_path, temp_path, queue_path, archive_path):
    invalid_pkg_files = []
    proc_id = datetime.now().isoformat()[11:16].replace(':', '')
    temp_path = temp_path + '/' + proc_id
    queue_path = queue_path + '/' + proc_id
    pkg_paths = []

    if os.path.isdir(temp_path):
        fs_utils.delete_file_or_folder(temp_path)
    if os.path.isdir(queue_path):
        fs_utils.delete_file_or_folder(queue_path)

    if archive_path is not None:
        if not os.path.isdir(archive_path):
            os.makedirs(archive_path)

    if not os.path.isdir(temp_path):
        os.makedirs(temp_path)

    for pkg_name in os.listdir(download_path):
        if is_valid_pkg_file(download_path + '/' + pkg_name):
            shutil.copyfile(download_path + '/' + pkg_name, temp_path + '/' + pkg_name)
        else:
            pkg_paths.append(pkg_name)
        fs_utils.delete_file_or_folder(download_path + '/' + pkg_name)

    for pkg_name in os.listdir(temp_path):
        queued_pkg_path = queue_path + '/' + pkg_name
        if not os.path.isdir(queued_pkg_path):
            os.makedirs(queued_pkg_path)

        if fs_utils.extract_package(temp_path + '/' + pkg_name, queued_pkg_path):
            if archive_path is not None:
                if os.path.isdir(archive_path):
                    shutil.copyfile(temp_path + '/' + pkg_name, archive_path + '/' + pkg_name)
            pkg_paths.append(queued_pkg_path)
        else:
            invalid_pkg_files.append(pkg_name)
            fs_utils.delete_file_or_folder(queued_pkg_path)
        fs_utils.delete_file_or_folder(temp_path + '/' + pkg_name)
    fs_utils.delete_file_or_folder(temp_path)

    return (pkg_paths, invalid_pkg_files)


def xml_converter_read_configuration(filename):
    r = None
    if os.path.isfile(filename):
        r = xc_config.XMLConverterConfiguration(filename)
        if not r.valid:
            r = None
    return r


def xml_converter_get_inputs(args):
    # python xml_converter.py <xml_src>
    # python xml_converter.py <collection_acron>
    package_path = None
    script = None
    collection_acron = None
    if len(args) == 2:
        script, param = args
        if os.path.isfile(param) or os.path.isdir(param):
            package_path = param
        else:
            collection_acron = param

    return (script, package_path, collection_acron)


def xml_converter_validate_inputs(package_path, collection_acron):
    # python xml_converter.py <xml_src>
    # python xml_converter.py <collection_acron>
    errors = []
    if package_path is None:
        if collection_acron is None:
            errors.append('Missing collection acronym')
    else:
        errors = xml_utils.is_valid_xml_path(package_path)
    return errors


def xml_config_filename(collection_acron):
    filename = CURRENT_PATH + '/../../scielo_paths.ini'

    if not os.path.isfile(filename):
        if not collection_acron is None:
            filename = CURRENT_PATH + '/../config/' + collection_acron + '.xc.ini'
    return filename


def is_valid_configuration_file(configuration_filename):
    messages = []
    if configuration_filename is None:
        messages.append('\n===== ATTENTION =====\n')
        messages.append('ERROR: No configuration file was informed')
    elif not os.path.isfile(configuration_filename):
        messages.append('\n===== ATTENTION =====\n')
        messages.append('ERROR: unable to read XML Converter configuration file: ' + configuration_filename)
    return messages


def is_valid_pkg_file(filename):
    return os.path.isfile(filename) and (filename.endswith('.zip') or filename.endswith('.tgz'))


def update_issue_copy(issue_db, issue_db_copy):
    d = os.path.dirname(issue_db_copy)
    if not os.path.isdir(d):
        os.makedirs(d)
    if not os.path.isfile(issue_db_copy + '.fst'):
        shutil.copyfile(CURRENT_PATH + '/issue.fst', issue_db_copy + '.fst')
    if open(CURRENT_PATH + '/issue.fst', 'r').read() != open(issue_db_copy + '.fst', 'r').read():
        shutil.copyfile(CURRENT_PATH + '/issue.fst', issue_db_copy + '.fst')
    shutil.copyfile(issue_db + '.mst', issue_db_copy + '.mst')
    shutil.copyfile(issue_db + '.xrf', issue_db_copy + '.xrf')


def call_converter(args, version='1.0'):
    script, package_path, collection_acron = xml_converter_get_inputs(args)
    if package_path is None and collection_acron is None:
        # GUI
        import xml_gui
        xml_gui.open_main_window(True, None)

    elif package_path is not None and collection_acron is not None:
        errors = xml_converter_validate_inputs(package_path, collection_acron)
        if len(errors) > 0:
            messages = []
            messages.append('\n===== ATTENTION =====\n')
            messages.append('ERROR: Incorrect parameters')
            messages.append('\nUsages:')
            messages.append('python xml_converter.py <xml_folder> | <collection_acron>')
            messages.append('where:')
            messages.append('  <xml_folder> = path of folder which contains')
            messages.append('  <collection_acron> = collection acron')

            messages.append('\n'.join(errors))
            print('\n'.join(messages))
        else:
            execute_converter(package_path, collection_acron)
    elif collection_acron is not None:
        execute_converter(package_path, collection_acron)
    elif package_path is not None:
        execute_converter(package_path)


def send_message(mailer, to, subject, text, attaches=None):
    if mailer is not None:
        #print('sending message ' + subject)
        mailer.send_message(to, subject, text, attaches)


def execute_converter(package_paths, collection_name=None):
    #collection_names = {'Brasil': 'scl', u'Salud Pública': 'spa'}
    collection_names = {}
    collection_acron = collection_names.get(collection_name)
    if collection_acron is None:
        collection_acron = collection_name

    config = xc.get_configuration(collection_acron)
    if config is not None:
        prepare_env(config)
        invalid_pkg_files = []
        bad_pkg_files = []
        bad_pkg_files_errors = []
        scilista = []

        mailer = xc.get_mailer(config)

        if package_paths is None:
            package_paths, invalid_pkg_files = queue_packages(config.download_path, config.temp_path, config.queue_path, config.archive_path)
        if package_paths is None:
            package_paths = []
        if not isinstance(package_paths, list):
            package_paths = [package_paths]

        for package_path in package_paths:
            package_folder = os.path.basename(package_path)
            print(package_path)
            report_location, scilista_item, acron_issue_label, results = convert_package(package_path)
            acron, issue_id = acron_issue_label.split(' ')
            #except Exception as e:
            #    print('ERROR!!!')
            #    print(e)
            #    bad_pkg_files.append(package_folder)
            #    bad_pkg_files_errors.append(str(e))
            #    report_location, report_path, scilista_item = [None, None, None]

            if scilista_item is not None:
                scilista.append(scilista_item)
                if config.is_enabled_transference:
                    transfer_website_files(acron, issue_id, config.local_web_app_path, config.transference_user, config.transference_server, config.remote_web_app_path)

            if report_location is not None:
                if config.is_windows:
                    pkg_reports.display_report(report_location)
                else:
                    link = converter_env.web_app_site + '/reports/' + acron + '/' + issue_id + '/' + os.path.basename(report_location)
                    report_location = '<html><body>' + html_reports.link(link, link) + '</body></html>'

                    transfer_report_files(acron, issue_id, config.local_web_app_path, config.transference_user, config.transference_server, config.remote_web_app_path)
                if config.email_subject_package_evaluation is not None:
                    send_message(mailer, config.email_to, config.email_subject_package_evaluation + ' ' + package_folder + ': ' + results, report_location)

        if len(invalid_pkg_files) > 0:
            send_message(mailer, config.email_to, config.email_subject_invalid_packages, config.email_text_invalid_packages + '\n'.join(invalid_pkg_files))
        if len(bad_pkg_files) > 0:
            send_message(mailer, config.email_to_adm, config.email_subject_invalid_packages, config.email_text_invalid_packages + '\n'.join(bad_pkg_files) + '\n'.join(bad_pkg_files_errors))

        if len(scilista) > 0 and config.collection_scilista is not None:
            open(config.collection_scilista, 'a+').write('\n'.join(scilista) + '\n')
    print('finished')


def prepare_env(config):
    global converter_env

    if converter_env is None:
        converter_env = ConverterEnv()

    converter_env.db_isis = dbm_isis.IsisDAO(dbm_isis.UCISIS(dbm_isis.CISIS(config.cisis1030), dbm_isis.CISIS(config.cisis1660)))

    update_issue_copy(config.issue_db, config.issue_db_copy)
    converter_env.db_isis.update_indexes(config.issue_db_copy, config.issue_db_copy + '.fst')
    converter_env.db_issue = xc_models.IssueDAO(converter_env.db_isis, config.issue_db_copy)

    import institutions_service

    org_manager = institutions_service.OrgManager()
    org_manager.load()

    converter_env.db_article = xc_models.ArticleDAO(converter_env.db_isis, org_manager)

    converter_env.local_web_app_path = config.local_web_app_path
    converter_env.serial_path = config.serial_path
    converter_env.version = '1.0'
    converter_env.is_windows = config.is_windows
    converter_env.web_app_site = config.web_app_site
    converter_env.skip_identical_xml = config.skip_identical_xml
    converter_env.max_fatal_error = config.max_fatal_error
    converter_env.max_error = config.max_error
    converter_env.max_warning = config.max_warning
