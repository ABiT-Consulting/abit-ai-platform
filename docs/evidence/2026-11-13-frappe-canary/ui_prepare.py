"""Prepare only own synthetic site's desk and private browser login."""
import os,json
from pathlib import Path
os.chdir('/home/frappe/frappe-bench/sites');import frappe
frappe.init('agov-uat.localhost');frappe.connect();frappe.set_user('Administrator')
frappe.db.set_single_value('System Settings','setup_complete',1)
frappe.defaults.set_global_default('company','AGOV Isolated UAT Company')
frappe.defaults.set_user_default('company','AGOV Isolated UAT Company',user='Administrator')
frappe.db.commit();frappe.destroy();print('OWN_SYNTHETIC_DESK_PREPARED')
