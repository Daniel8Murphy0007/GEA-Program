# LAS format log file from PETREL
# Project units are specified as depth units
#==================================================================
~Version information
VERS.   2.0:
WRAP.   YES:
#==================================================================
~WELL INFORMATION
#MNEM.UNIT      DATA             DESCRIPTION
#---- ------ --------------   -----------------------------
STRT .M      1.0668          :START DEPTH
STOP .M      1939.13760      :STOP DEPTH
STEP .M       0.15240        :STEP
NULL .          -999.25      :NULL VALUE
COMP .        Elmworth Energy Corporation              :COMPANY
WELL .        Kennetcook #2                            :WELL
FLD  .        Windsor Block                            :FIELD
LOC  .        Lat = 45* 12' 34.237" N                  :LOCATION
PROV .        Nova Scotia                              :PROVINCE
  UWI.        Long = 63* 45'24.460  W                  :UNIQUE WELL ID
LIC  .        P-129                                    :LICENSE NUMBER
CTRY .        CA                                       :COUNTRY (WWW code)
 DATE.        10-Oct-2007                              :LOG DATE {DD-MMM-YYYY}
SRVC .        Schlumberger                             :SERVICE COMPANY
#==================================================================
~PARAMETER INFORMATION
#MNEM.UNIT    VALUE                      DESCRIPTION
#---- -----   --------------------       ------------------------
DREF .        KELLY BUSHING              :Drilling Measured From
RUN  .        1                          :Run Number
CSGD .M        280.00000                 :Casing Bottom of Driller
CSGL .M        280.00000                 :Casing Bottom of Logger
CSGS .MM       244.50000                 :Current Casing Size
MUDD .K/M3    1110.00000                 :Drilling Fluid Density
MUD  .        Gel-Chem                   :Drilling Fluid Type
GL   .M         90.30000                 :Elevation of Ground Level
EREF .M         94.80000                 :Elevation of Kelly Bushing
RMCT .DEGC      19.40000                 :Mud Cake Sample Temperature
RMFT .DEGC      16.90000                 :Mud Filtrate Sample Temperature
TMAX .DEGC      42.00000                 :Maximum Recorded Temperature
RMT  .DEGC      18.00000                 :Mud Sample Temperature
RMC  .OHMM       0.23400                 :Resistivity of Mud Cake Sample
RMF  .OHMM       0.15740                 :Resistivity of Mud Filtrate Sample
RM   .OHMM       0.16370                 :Resistivity of Mud Sample
BHT  .DEGC      42.00000                 :Bottom Hole Temperature (used in calculations)
BLI  .M       1935.00000                 :Bottom Log Interval
BS   .MM       200.00000                 :Bit Size
MRT1 .DEGC      42.00000                 :Maximum Recorded Temperature 1
MATR .        SAND                       :Rock Matrix for Neutron Porosity Corrections
MDEN .K/M3    2650.00000                 :Matrix Density
RMB  .OHMM       0.10187                 :Resistivity of Mud - BHT
RMFB .OHMM       0.09522                 :Resistivity of Mud Filtrate - BHT
TDD  .M       1935.00000                 :Total Depth - Driller
TDL  .M       1935.00000                 :Total Depth - Logger
TLI  .M        280.00000                 :Top Log Interval
WN   .        Kennetcook #2              :Well Name
EPD  .M          90.300003               :Elevation of Permanent Datum above Mean Sea Level
#=======================================================================
~Curve
DEPT .m                   : DEPTH
CALI .in                  : Caliper
HCAL .in                  : HRCC Cal. Caliper
PEF .                     : Photoelectric Factor
DT .us/ft                 : DT
DTS .us/ft                : DTS
DPHI_SAN .m3/m3           : Density Porosity (matrix Sandstone)
DPHI_LIM .m3/m3           : Density Porosity (matrix Limestone)
DPHI_DOL .m3/m3           : Density Porosity (matrix Dolomite)
NPHI_SAN .m3/m3           : Thermal Neutron Porosity (matrix Sandstone)
NPHI_LIM .m3/m3           : Thermal Neutron Porosity (matrix Limestone)
NPHI_DOL .m3/m3           : Thermal Neutron Porosity (matrix Dolomite)
RLA5 .ohm.m               : HRLT Borehole Corrected Resistivity 5
RLA3 .ohm.m               : HRLT Borehole Corrected Resistivity 3
RLA4 .ohm.m               : HRLT Borehole Corrected Resistivity 4
RLA1 .ohm.m               : HRLT Borehole Corrected Resistivity 1
RLA2 .ohm.m               : HRLT Borehole Corrected Resistivity 2
RXOZ .ohm.m               : MCFL Standard Resolution Invaded Zone Resistivity
RXO_HRLT .ohm.m           :
RT_HRLT .ohm.m            : HRLT Computed True Resistivity
RM_HRLT .ohm.m            : HRLT Mud Resistivity
DRHO .g/cm3               : HRDD Density Correction
RHOB .g/cm3               : Bulk Density
GR .gAPI                  : Gamma-Ray
SP .mV                    : SP
#==================================================================
~Ascii
# VERBATIM EXCERPT of the public file (see .provenance.json): full header +
# the first 10 WRAPPED records (1.0668-2.4384 m). Full file spans
# 1.0668-1939.14 m. WRAP. YES: each record = depth line + 4 value lines.
 1.0668000000
 2.4438154697 4.3912849426 3.5864000320  -999.250000  -999.250000 0.1574800014
 0.1984400004 0.2590999901 0.4650999904 0.3364700079 0.3018600047 0.0255800001
 0.0267500002 0.0256200004 0.0320999995 0.0279399995 0.0576100014 0.0255800001
 0.0255800001 0.0550099984 0.1942329407 2.3901498318 46.698650360 120.12500000
 1.2192000000
 2.4438154697 4.3912849426 3.5864000320  -999.250000  -999.250000 0.1574800014
 0.1984400004 0.2590999901 0.4650999904 0.3364700079 0.3018600047 0.0255800001
 0.0267500002 0.0256200004 0.0320999995 0.0279399995 0.0576100014 0.0255800001
 0.0255800001 0.0550099984 0.1942329407 2.3901498318 46.698650360 120.12500000
 1.3716000000
 2.4438154697 4.3912849426 3.5864000320  -999.250000  -999.250000 0.1574800014
 0.1984400004 0.2590999901 0.4650999904 0.3364700079 0.3018600047 0.0255800001
 0.0267500002 0.0256200004 0.0320999995 0.0279399995 0.0576100014 0.0255800001
 0.0255800001 0.0550099984 0.1942329407 2.3901498318 46.698650360 120.12500000
 1.5240000000
 2.4438154697 4.3912849426 3.5864000320  -999.250000  -999.250000 0.1574800014
 0.1984400004 0.2590999901 0.4650999904 0.3364700079 0.3018600047 0.0255800001
 0.0267500002 0.0256200004 0.0320999995 0.0279399995 0.0576100014 0.0255800001
 0.0255800001 0.0550099984 0.1942329407 2.3901498318 46.698650360 120.12500000
 1.6764000000
 2.4438154697 4.3912849426 3.5864000320  -999.250000  -999.250000 0.1574800014
 0.1984400004 0.2590999901 0.4650999904 0.3364700079 0.3018600047 0.0255800001
 0.0267500002 0.0256200004 0.0320999995 0.0279399995 0.0576100014 0.0255800001
 0.0255800001 0.0550099984 0.1942329407 2.3901498318 46.698650360 120.12500000
 1.8288000000
 2.4438154697 4.3912849426 3.5864000320  -999.250000  -999.250000 0.1574800014
 0.1984400004 0.2590999901 0.4650999904 0.3364700079 0.3018600047 0.0255800001
 0.0267500002 0.0256200004 0.0320999995 0.0279399995 0.0576100014 0.0255800001
 0.0255800001 0.0550099984 0.1942329407 2.3901498318 46.698650360 120.12500000
 1.9812000000
 2.4438154697 4.3912849426 3.5864000320  -999.250000  -999.250000 0.1574800014
 0.1984400004 0.2590999901 0.4650999904 0.3364700079 0.3018600047 0.0255800001
 0.0267500002 0.0256200004 0.0320999995 0.0279399995 0.0576100014 0.0255800001
 0.0255800001 0.0550099984 0.1942329407 2.3901498318 46.698650360 120.12500000
 2.1336000000
 2.4438154697 4.3912849426 3.5864000320  -999.250000  -999.250000 0.1574800014
 0.1984400004 0.2590999901 0.4650999904 0.3364700079 0.3018600047 0.0255800001
 0.0267500002 0.0256200004 0.0320999995 0.0279399995 0.0576100014 0.0255800001
 0.0255800001 0.0550099984 0.1942329407 2.3901498318 46.698650360 120.12500000
 2.2860000000
 2.4438154697 4.3912849426 3.5864000320  -999.250000  -999.250000 0.1574800014
 0.1984400004 0.2590999901 0.4650999904 0.3364700079 0.3018600047 0.0255800001
 0.0267500002 0.0256200004 0.0320999995 0.0279399995 0.0576100014 0.0255800001
 0.0255800001 0.0550099984 0.1942329407 2.3901498318 46.698650360 120.12500000
 2.4384000000
 2.4438154697 4.3912849426 3.5864000320  -999.250000  -999.250000 0.1574800014
 0.1984400004 0.2590999901 0.4650999904 0.3364700079 0.3018600047 0.0255800001
 0.0267500002 0.0256200004 0.0320999995 0.0279399995 0.0576100014 0.0255800001
 0.0255800001 0.0550099984 0.1942329407 2.3901498318 46.698650360 120.12500000
