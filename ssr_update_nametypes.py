#!/usr/bin/env python3
# -*- coding: utf8

# ssr_update_nametypes.py
# Updates SSR name categories from Excel sheet vs json stucture on GitHub.
# No parameters.


import copy
import json
import urllib.request
import csv
import sys


version = "0.2.0"
out_filename = "navnetyper_tagged_updated.json"
update_description = False


# Main program

if __name__ == '__main__':

	print("\nUpdate SSR tagging in GitHub json")

	'''
	# Load official name categories from GeoNorge

	filename = "https://ws.geonorge.no/stedsnavn/v1/navneobjekttyper"
	file = urllib.request.urlopen(filename)
	name_types = json.load(file)
	file.close()

	names = {}
	for name in name_types:
		names[ name['navneobjekttypekode'] ] = name['navneobjekttype']
	'''

	filename = "https://register.geonorge.no/sosi-kodelister/stedsnavn/navneobjekttype.json?"
	file = urllib.request.urlopen(filename)
	name_types = json.load(file)
	file.close()

	types = {}
	for name in name_types['containeditems']:
		types[ name['codevalue'] ] = name['description']

	filename = "https://register.geonorge.no/sosi-kodelister/stedsnavn/navneobjektgruppe.json?"
	file = urllib.request.urlopen(filename)
	name_groups = json.load(file)
	file.close()

	groups = {}
	for group in name_groups['containeditems']:
		groups[group['codevalue']] = group['description']

	filename = "https://register.geonorge.no/sosi-kodelister/stedsnavn/navneobjekthovedgruppe.json?"
	file = urllib.request.urlopen(filename)
	main_name_groups = json.load(file)
	file.close()

	main_groups = {}
	for group in main_name_groups['containeditems']:
		main_groups[group['codevalue']] = group['description'].replace('""', "'").replace('"', '')

	# Load CSV tagging table

	filename = "Tagging tabell SSR2.csv" 
	file = open(filename)
	csv_reader = csv.DictReader(file, delimiter=";")
	fieldnames = csv_reader.fieldnames.copy()

	print ("\tFields: %s\n" % (", ".join(fieldnames)))

	tagging = {}

	for row in csv_reader:
		name_type = row['SSR2 navnetype'].strip()
		if name_type:
			main_tag = row['OSM tag'].replace(" ", "").split(";")
			locality_tag = [row['tillegg'].replace(" ", "")]
			fixme_tag = [row['fixme'].strip()]
			tags = {} 
			for tag in main_tag + locality_tag + fixme_tag:
				if tag:
					if "=" not in tag:
						print("Not proper tagging format for '%s': %s" % (name_type, tag))
					else:
						tag_split = tag.split("=", 1)
						tags[ tag_split[0].strip() ] = tag_split[1].strip()

			if not tags:
				print ("No tagging for '%s'" % name_type)
			tagging[ name_type ] = tags

	# Load json to validate from GitHub

#	filename = "https://raw.githubusercontent.com/NKAmapper/geocode2osm/master/navnetyper.json"
	filename = "https://raw.githubusercontent.com/NKAmapper/ssr2osm/main/navnetyper_tagged.json"
	file = urllib.request.urlopen(filename)
	name_type_master = json.load(file)
	file.close()

	# Iterate and discover differences

	edit = False

	for main_group in name_type_master['navnetypeHovedgrupper']:

		if update_description:
			# Maintain dict order
			list_copy = copy.copy(main_group['navnetypeGrupper'])
			del main_group['navnetypeGrupper']
			main_group['beskrivelse'] = main_groups[ main_group['navn'] ]
			main_group['navnetypeGrupper'] = list_copy

		for group in main_group['navnetypeGrupper']:

			if update_description:
				# Maintain dict order
				list_copy = copy.copy(group['navnetyper'])
				del group['navnetyper']
				group['beskrivelse'] = groups[ group['navn'] ]
				group['navnetyper'] = list_copy

			for name_type in group['navnetyper']:
				if update_description:
					name_type['beskrivelse'] = types[ name_type['navn'] ]

				if name_type['navn'] in tagging:
					if "tags" in name_type:
						if name_type['tags'] != tagging[ name_type['navn'] ]:
							diff = dict(set(tagging[ name_type['navn']].items()) ^ set(name_type['tags'].items()))
							if diff:
								print ("Updates '%s': %s" % (name_type['navn'], diff))
								edit = True
					else:
						print ("Adds '%s': %s" % (name_type['navn'], tagging[ name_type['navn'] ]))
						edit = True

					name_type['tags'] = tagging[ name_type['navn'] ]
					del tagging[ name_type['navn'] ]
				else:
					print ("Missing tags for '%s" % name_type['navn'])

	# Display any name types/groups not used

	if tagging:
		print ("\nTypes not used:\n\t%s" % "\n\t".join(list(tagging.keys())))

	# Output json file

	if edit or update_description:
		file = open(out_filename, "w")
		json.dump(name_type_master, file, indent = 2, ensure_ascii=False)
		file.close()
		print ("\nSaved json to '%s'\n" % out_filename)
		sys.exit()
	else:
		print ("Tagged json ok\n")
