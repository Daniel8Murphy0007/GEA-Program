# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""GEA Driller - the directional driller's agent: a reinforcement-learning policy trained in a physics twin of the
lateral (the formation, the steering tool, the bit, the hydraulics, the hole), run on the rig PC against the rig's
own feed, recommending in operational units - and, when an operator arms it for a section, commanding a rotary
steerable system through a downlink port that ships disconnected. Feeds GEA-Program."""
NAME = "GEA Driller"
__version__ = "0.83.1"
