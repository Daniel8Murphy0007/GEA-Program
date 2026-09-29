from gea_program import Layer, SubsurfaceModel
from gea_program.cli import main


def test_simulation_metrics_are_positive():
    model = SubsurfaceModel(
        [
            Layer("Shale cap", 300.0, 0.08, 12.0),
            Layer("Sandstone", 1000.0, 0.24, 220.0),
            Layer("Limestone", 500.0, 0.14, 85.0),
        ]
    )

    result = model.simulate_well(well_depth_ft=4200.0, flow_rate_bpd=1800.0)

    assert model.total_thickness_ft == 1800.0
    assert 0.0 < model.average_porosity < 1.0
    assert model.average_permeability_md > 0.0
    assert result.reservoir_pressure_psi > result.bottom_hole_pressure_psi
    assert result.productivity_index > 0.0
    assert 0.0 < result.estimated_recovery_pct <= 95.0


def test_cli_prints_report(capsys):
    exit_code = main(["--well-depth-ft", "3500", "--flow-rate-bpd", "1200"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "GEA-Program Subsurface Report" in captured.out
    assert "Reservoir pressure:" in captured.out
    assert "Estimated recovery:" in captured.out
